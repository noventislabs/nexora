"""YouTube Data API v3 provider.

Authentication is OAuth 2.0 with PKCE. NEXORA never sees, transports or stores a
YouTube password — the operator authenticates with Google directly and Google returns
an authorization code.

Uploads use the resumable protocol, so a large render is streamed in chunks rather
than buffered in memory, and a dropped connection resumes instead of restarting.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, BinaryIO
from urllib.parse import urlencode

import httpx

from nexora.config import settings
from nexora.core.crypto import b64url
from nexora.core.errors import (
    ProviderUnavailable,
    UpstreamPermanentError,
    ValidationError,
)
from nexora.core.logging import get_logger
from nexora.db.models.enums import ComponentStatus
from nexora.services.http import http_client, raise_for_upstream, request_json
from nexora.services.providers.base import Availability
from nexora.services.providers.youtube.base import (
    OAuthTokens,
    RemoteChannel,
    RemoteVideo,
    UploadResult,
    YouTubeProvider,
    youtube_registry,
)

logger = get_logger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API_ROOT = "https://www.googleapis.com/youtube/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

#: Least privilege: upload and read our own channel. `youtube.force-ssl` is required
#: for thumbnail upload. No scope here can read another user's private data.
SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
)
#: Requested separately, because analytics is a distinct consent decision.
ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
MONETARY_SCOPE = "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"

#: 8 MiB keeps peak memory low on the 8 GB target machine while staying well above
#: the 256 KiB granularity the resumable protocol requires.
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024
MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024

VALID_PRIVACY = ("private", "unlisted", "public")


def generate_pkce_verifier() -> str:
    return secrets.token_urlsafe(64)[:128]


def pkce_challenge(verifier: str) -> str:
    return b64url(hashlib.sha256(verifier.encode()).digest())


@youtube_registry.register
class GoogleYouTubeProvider(YouTubeProvider):
    name = "google"

    def __init__(self, *, include_analytics: bool = True, include_monetary: bool = False):
        self.include_analytics = include_analytics
        self.include_monetary = include_monetary

    # ------------------------------------------------------------- availability
    def availability(self) -> Availability:
        missing = []
        if not settings.youtube_client_id:
            missing.append("YOUTUBE_CLIENT_ID")
        if not settings.youtube_client_secret:
            missing.append("YOUTUBE_CLIENT_SECRET")
        if missing:
            return Availability.not_configured(
                provider=self.name,
                missing=tuple(missing),
                detail=(
                    "Create an OAuth 2.0 'Web application' client in Google Cloud Console "
                    "with the YouTube Data API v3 enabled, add "
                    f"{settings.youtube_redirect_uri} as an authorized redirect URI, then "
                    "set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET."
                ),
            )
        from nexora.core.crypto import encryption_available

        if not encryption_available():
            return Availability.unavailable(
                provider=self.name,
                detail=(
                    "ENCRYPTION_KEY is not set. OAuth tokens are only ever stored encrypted, "
                    "so connecting a channel is blocked until a key is configured."
                ),
            )
        return Availability(
            status=ComponentStatus.AVAILABLE,
            provider=self.name,
            detail="OAuth client configured",
            metadata={"redirect_uri": settings.youtube_redirect_uri, "scopes": list(self.scopes)},
        )

    @property
    def scopes(self) -> tuple[str, ...]:
        scopes = list(SCOPES)
        if self.include_analytics:
            scopes.append(ANALYTICS_SCOPE)
        if self.include_monetary:
            scopes.append(MONETARY_SCOPE)
        return tuple(scopes)

    # -------------------------------------------------------------------- OAuth
    def authorization_url(self, *, state: str, code_challenge: str) -> str:
        self.require()
        query = {
            "client_id": settings.youtube_client_id,
            "redirect_uri": settings.youtube_redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            # Required to receive a refresh token, and to re-issue one on reconnect.
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        }
        return f"{AUTH_URL}?{urlencode(query)}"

    def exchange_code(self, *, code: str, code_verifier: str) -> OAuthTokens:
        self.require()
        payload = self._token_request(
            {
                "code": code,
                "client_id": settings.youtube_client_id,
                "client_secret": settings.youtube_client_secret,
                "redirect_uri": settings.youtube_redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            }
        )
        refresh = payload.get("refresh_token")
        if not refresh:
            raise ProviderUnavailable(
                "Google did not return a refresh token. Without one the connection would "
                "break in an hour. Revoke NEXORA's access in your Google account and "
                "reconnect so consent is granted again."
            )
        return self._tokens_from(payload, refresh_token=refresh)

    def refresh_tokens(self, refresh_token: str) -> OAuthTokens:
        self.require()
        payload = self._token_request(
            {
                "refresh_token": refresh_token,
                "client_id": settings.youtube_client_id,
                "client_secret": settings.youtube_client_secret,
                "grant_type": "refresh_token",
            }
        )
        # A refresh response usually omits refresh_token; the existing one stays valid.
        return self._tokens_from(payload, refresh_token=payload.get("refresh_token") or refresh_token)

    def _token_request(self, form: dict[str, Any]) -> dict[str, Any]:
        with http_client(timeout=60.0) as client:
            try:
                response = client.post(TOKEN_URL, data=form)
            except httpx.HTTPError as exc:
                raise ProviderUnavailable(f"Google's token endpoint is unreachable: {exc}") from exc
            raise_for_upstream(response, provider="Google OAuth")
            try:
                return response.json()
            except ValueError as exc:
                raise ProviderUnavailable("Google returned a non-JSON token response.") from exc

    @staticmethod
    def _tokens_from(payload: dict[str, Any], *, refresh_token: str | None) -> OAuthTokens:
        access = payload.get("access_token")
        if not access:
            raise ProviderUnavailable("Google returned no access token.")
        expires_in = int(payload.get("expires_in", 3600))
        return OAuthTokens(
            access_token=str(access),
            refresh_token=refresh_token,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
            scopes=tuple(str(payload.get("scope", "")).split()),
            token_type=str(payload.get("token_type", "Bearer")),
        )

    def revoke(self, token: str) -> None:
        with http_client(timeout=30.0) as client:
            try:
                response = client.post(REVOKE_URL, data={"token": token})
            except httpx.HTTPError as exc:
                raise ProviderUnavailable(f"Google's revoke endpoint is unreachable: {exc}") from exc
        # 400 means the token was already invalid, which is the outcome we wanted.
        if response.status_code not in (200, 400):
            raise_for_upstream(response, provider="Google OAuth revoke")

    # ------------------------------------------------------------------ reading
    def get_my_channel(self, access_token: str) -> RemoteChannel:
        payload = self._api(
            access_token,
            "GET",
            f"{API_ROOT}/channels",
            params={"part": "snippet,statistics,contentDetails", "mine": "true"},
        )
        items = payload.get("items") or []
        if not items:
            raise UpstreamPermanentError(
                "The authorized Google account has no YouTube channel. Create a channel "
                "first, then reconnect."
            )
        return _remote_channel(items[0])

    def get_video(self, access_token: str, video_id: str) -> RemoteVideo | None:
        payload = self._api(
            access_token,
            "GET",
            f"{API_ROOT}/videos",
            params={"part": "snippet,status,contentDetails,statistics", "id": video_id},
        )
        items = payload.get("items") or []
        return _remote_video(items[0]) if items else None

    def list_channel_videos(self, access_token: str, *, limit: int = 25) -> list[RemoteVideo]:
        channel = self.get_my_channel(access_token)
        if not channel.uploads_playlist_id:
            return []
        try:
            playlist = self._api(
                access_token,
                "GET",
                f"{API_ROOT}/playlistItems",
                params={
                    "part": "contentDetails",
                    "playlistId": channel.uploads_playlist_id,
                    "maxResults": max(1, min(limit, 50)),
                },
            )
        except UpstreamPermanentError:
            # An empty uploads playlist 404s rather than returning zero items.
            return []

        video_ids = [
            item["contentDetails"]["videoId"]
            for item in playlist.get("items", [])
            if item.get("contentDetails", {}).get("videoId")
        ]
        if not video_ids:
            return []
        payload = self._api(
            access_token,
            "GET",
            f"{API_ROOT}/videos",
            params={
                "part": "snippet,status,contentDetails,statistics",
                "id": ",".join(video_ids),
            },
        )
        return [_remote_video(item) for item in payload.get("items", [])]

    # ---------------------------------------------------------------- uploading
    def upload_video(
        self,
        access_token: str,
        *,
        media: BinaryIO,
        media_size: int,
        title: str,
        description: str,
        tags: list[str],
        privacy_status: str,
        category_id: str | None = None,
        publish_at: datetime | None = None,
        made_for_kids: bool = False,
        language: str = "en",
    ) -> UploadResult:
        """Upload via the resumable protocol, streaming the file in chunks."""
        self.require()
        if privacy_status not in VALID_PRIVACY:
            raise ValidationError(f"privacy_status must be one of {VALID_PRIVACY}.")
        if media_size <= 0:
            raise ValidationError("The render is empty; there is nothing to upload.")
        if publish_at is not None and privacy_status != "private":
            # YouTube only honours publishAt on a private video.
            raise ValidationError(
                "A scheduled publish time requires privacy_status='private'; YouTube makes "
                "the video public itself at that time."
            )

        body: dict[str, Any] = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags[:critical_tag_limit(tags)],
                "defaultLanguage": language,
                "defaultAudioLanguage": language,
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": made_for_kids,
                "embeddable": True,
                "license": "youtube",
            },
        }
        if category_id:
            body["snippet"]["categoryId"] = str(category_id)
        if publish_at is not None:
            body["status"]["publishAt"] = publish_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        session_url = self._start_resumable_session(access_token, body, media_size)
        payload = self._upload_chunks(session_url, media, media_size)

        video_id = payload.get("id")
        if not video_id:
            raise ProviderUnavailable("YouTube accepted the upload but returned no video id.")
        status = payload.get("status") or {}
        return UploadResult(
            video_id=str(video_id),
            upload_status=str(status.get("uploadStatus", "uploaded")),
            privacy_status=status.get("privacyStatus"),
            published_at=_parse_iso((payload.get("snippet") or {}).get("publishedAt")),
            raw=payload,
        )

    def _start_resumable_session(
        self, access_token: str, body: dict[str, Any], media_size: int
    ) -> str:
        with http_client(timeout=120.0) as client:
            try:
                response = client.post(
                    UPLOAD_URL,
                    params={"part": "snippet,status", "uploadType": "resumable"},
                    json=body,
                    headers={
                        "authorization": f"Bearer {access_token}",
                        "content-type": "application/json; charset=UTF-8",
                        "x-upload-content-length": str(media_size),
                        "x-upload-content-type": "video/mp4",
                    },
                )
            except httpx.HTTPError as exc:
                raise ProviderUnavailable(f"YouTube upload could not be started: {exc}") from exc
            raise_for_upstream(response, provider="YouTube upload")

        session_url = response.headers.get("location")
        if not session_url:
            raise ProviderUnavailable(
                "YouTube did not return a resumable upload session URL."
            )
        return session_url

    def _upload_chunks(self, session_url: str, media: BinaryIO, media_size: int) -> dict[str, Any]:
        uploaded = 0
        with http_client(timeout=600.0) as client:
            while uploaded < media_size:
                chunk = media.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    raise ProviderUnavailable(
                        f"The render ended after {uploaded} of {media_size} bytes."
                    )
                end = uploaded + len(chunk) - 1
                try:
                    response = client.put(
                        session_url,
                        content=chunk,
                        headers={
                            "content-length": str(len(chunk)),
                            "content-range": f"bytes {uploaded}-{end}/{media_size}",
                        },
                    )
                except httpx.HTTPError as exc:
                    raise ProviderUnavailable(
                        f"The upload connection failed after {uploaded} bytes: {exc}"
                    ) from exc

                if response.status_code in (200, 201):
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise ProviderUnavailable(
                            "YouTube finished the upload but returned a non-JSON response."
                        ) from exc

                if response.status_code == 308:
                    # Google reports how much it actually holds; trust that over our count.
                    uploaded = _resume_offset(response.headers.get("range"), fallback=end + 1)
                    if uploaded != end + 1:
                        media.seek(uploaded)
                    continue

                raise_for_upstream(response, provider="YouTube upload")

        raise ProviderUnavailable("The upload completed without YouTube confirming the video.")

    def set_thumbnail(
        self, access_token: str, *, video_id: str, image: bytes, mime_type: str
    ) -> None:
        self.require()
        if len(image) > MAX_THUMBNAIL_BYTES:
            raise ValidationError(
                f"YouTube's thumbnail limit is 2 MB; this image is "
                f"{len(image) / 1_048_576:.1f} MB."
            )
        with http_client(timeout=120.0) as client:
            try:
                response = client.post(
                    "https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
                    params={"videoId": video_id, "uploadType": "media"},
                    content=image,
                    headers={
                        "authorization": f"Bearer {access_token}",
                        "content-type": mime_type,
                    },
                )
            except httpx.HTTPError as exc:
                raise ProviderUnavailable(f"The thumbnail upload failed: {exc}") from exc
            raise_for_upstream(response, provider="YouTube thumbnail")

    # ------------------------------------------------------------------ helpers
    def _api(
        self, access_token: str, method: str, url: str, **kwargs: Any
    ) -> dict[str, Any]:
        with http_client(timeout=60.0) as client:
            return request_json(
                client,
                method,
                url,
                provider="YouTube Data API",
                headers={"authorization": f"Bearer {access_token}"},
                **kwargs,
            )


def critical_tag_limit(tags: list[str]) -> int:
    """YouTube caps the tags field at 500 characters in total."""
    total = 0
    for index, tag in enumerate(tags):
        total += len(tag) + 1
        if total > 500:
            return index
    return len(tags)


def _resume_offset(range_header: str | None, *, fallback: int) -> int:
    if not range_header or "-" not in range_header:
        return fallback
    try:
        return int(range_header.rsplit("-", 1)[1]) + 1
    except (ValueError, IndexError):
        return fallback


def _remote_channel(item: dict[str, Any]) -> RemoteChannel:
    snippet = item.get("snippet") or {}
    statistics = item.get("statistics") or {}
    hidden = bool(statistics.get("hiddenSubscriberCount"))
    return RemoteChannel(
        id=str(item.get("id", "")),
        title=str(snippet.get("title", "")),
        custom_url=snippet.get("customUrl"),
        # A hidden subscriber count is absent, not zero.
        subscriber_count=(
            _as_int(statistics.get("subscriberCount")) if not hidden else None
        ),
        subscriber_count_hidden=hidden,
        video_count=_as_int(statistics.get("videoCount")),
        view_count=_as_int(statistics.get("viewCount")),
        uploads_playlist_id=(item.get("contentDetails") or {})
        .get("relatedPlaylists", {})
        .get("uploads"),
    )


def _remote_video(item: dict[str, Any]) -> RemoteVideo:
    snippet = item.get("snippet") or {}
    status = item.get("status") or {}
    statistics = item.get("statistics") or {}
    return RemoteVideo(
        id=str(item.get("id", "")),
        title=str(snippet.get("title", "")),
        description=snippet.get("description"),
        tags=list(snippet.get("tags") or []),
        privacy_status=status.get("privacyStatus"),
        upload_status=status.get("uploadStatus"),
        published_at=_parse_iso(snippet.get("publishedAt")),
        duration_seconds=_parse_duration((item.get("contentDetails") or {}).get("duration")),
        thumbnail_url=((snippet.get("thumbnails") or {}).get("high") or {}).get("url"),
        # Only what the API returned; a hidden metric stays absent.
        statistics={
            key: value
            for key, value in (
                ("views", _as_int(statistics.get("viewCount"))),
                ("likes", _as_int(statistics.get("likeCount"))),
                ("comments", _as_int(statistics.get("commentCount"))),
            )
            if value is not None
        },
        raw=item,
    )


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_duration(value: Any) -> int | None:
    """Parse an ISO-8601 duration such as PT12M31S."""
    if not isinstance(value, str) or not value.startswith("PT"):
        return None
    import re

    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value)
    if not match:
        return None
    hours, minutes, seconds = (int(part) if part else 0 for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds
