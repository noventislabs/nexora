"""Synthetic feed and API payloads — TEST FIXTURES, never production data.

These mirror the documented response shapes of each upstream so the adapters can be
exercised without network access. They are deliberately *not* realistic-looking trend
data: titles are obviously synthetic so a fixture leaking into a UI would be noticed.
"""

RSS_2_0 = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>TEST FIXTURE Feed</title>
    <link>https://fixture.invalid/</link>
    <description>Synthetic feed used only by the NEXORA test suite.</description>
    <item>
      <title>TEST FIXTURE: semiconductor supply chain analysis</title>
      <link>https://fixture.invalid/articles/1</link>
      <description>&lt;p&gt;Synthetic &lt;b&gt;summary&lt;/b&gt; body for parser testing.&lt;/p&gt;</description>
      <pubDate>Mon, 15 Sep 2026 08:30:00 GMT</pubDate>
      <guid isPermaLink="false">fixture-item-1</guid>
      <author>fixture@fixture.invalid (Fixture Author)</author>
      <category>technology</category>
    </item>
    <item>
      <title>TEST FIXTURE: quantum error correction milestone</title>
      <link>https://fixture.invalid/articles/2</link>
      <description>Second synthetic item.</description>
      <pubDate>Tue, 16 Sep 2026 11:00:00 GMT</pubDate>
      <guid isPermaLink="false">fixture-item-2</guid>
    </item>
    <item>
      <title></title>
      <link>https://fixture.invalid/articles/3</link>
      <guid isPermaLink="false">fixture-item-3-no-title</guid>
    </item>
  </channel>
</rss>
"""

ATOM_1_0 = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>TEST FIXTURE Atom Feed</title>
  <link href="https://fixture.invalid/atom"/>
  <updated>2026-09-16T12:00:00Z</updated>
  <id>urn:uuid:fixture-atom-feed</id>
  <entry>
    <title>TEST FIXTURE: atom entry about energy storage</title>
    <link href="https://fixture.invalid/atom/1"/>
    <id>urn:uuid:fixture-atom-1</id>
    <updated>2026-09-16T09:15:00Z</updated>
    <published>2026-09-16T09:00:00Z</published>
    <summary>Synthetic atom summary.</summary>
    <author><name>Fixture Atom Author</name></author>
  </entry>
  <entry>
    <title>TEST FIXTURE: atom entry about grid economics</title>
    <link href="https://fixture.invalid/atom/2"/>
    <id>urn:uuid:fixture-atom-2</id>
    <updated>2026-09-16T10:30:00Z</updated>
  </entry>
</feed>
"""

MALFORMED_FEED = "<rss><channel><item><title>unterminated"

EMPTY_FEED = """<?xml version="1.0"?><rss version="2.0"><channel>
<title>TEST FIXTURE empty</title></channel></rss>"""

YOUTUBE_MOST_POPULAR = {
    "kind": "youtube#videoListResponse",
    "items": [
        {
            "id": "FIXTUREVID1",
            "snippet": {
                "publishedAt": "2026-09-17T10:00:00Z",
                "channelId": "UCfixture1",
                "title": "TEST FIXTURE: video with full statistics",
                "description": "Synthetic description for adapter testing.",
                "channelTitle": "Fixture Channel",
                "categoryId": "28",
                "tags": ["fixture", "testing"],
                "defaultAudioLanguage": "en",
            },
            "statistics": {"viewCount": "125000", "likeCount": "4100", "commentCount": "380"},
            "contentDetails": {"duration": "PT12M31S"},
        },
        {
            # A channel that hides likes: the key is simply absent from the response.
            "id": "FIXTUREVID2",
            "snippet": {
                "publishedAt": "2026-09-17T14:20:00Z",
                "channelId": "UCfixture2",
                "title": "TEST FIXTURE: video with hidden like count",
                "description": "",
                "channelTitle": "Fixture Channel Two",
                "categoryId": "28",
            },
            "statistics": {"viewCount": "9400", "commentCount": "12"},
            "contentDetails": {"duration": "PT4M2S"},
        },
        {"id": "FIXTUREVID3", "snippet": {"title": ""}},
    ],
}

YOUTUBE_SEARCH = {
    "kind": "youtube#searchListResponse",
    "items": [
        {"id": {"kind": "youtube#video", "videoId": "FIXTUREVID1"}, "snippet": {"title": "x"}},
        {"id": {"kind": "youtube#channel", "channelId": "UCnope"}, "snippet": {"title": "y"}},
    ],
}

YOUTUBE_QUOTA_ERROR = {
    "error": {
        "code": 403,
        "message": "The request cannot be completed because you have exceeded your quota.",
        "errors": [{"reason": "quotaExceeded"}],
    }
}

REDDIT_TOKEN = {"access_token": "fixture-token", "token_type": "bearer", "expires_in": 3600}

REDDIT_LISTING = {
    "kind": "Listing",
    "data": {
        "children": [
            {
                "kind": "t3",
                "data": {
                    "id": "fixturepost1",
                    "title": "TEST FIXTURE: discussion of chip fabrication economics",
                    "permalink": "/r/fixture/comments/fixturepost1/",
                    "selftext": "Synthetic body text.",
                    "author": "fixture_user",
                    "created_utc": 1789000000,
                    "score": 2450,
                    "num_comments": 318,
                    "upvote_ratio": 0.94,
                    "subreddit": "fixture",
                    "stickied": False,
                    "over_18": False,
                },
            },
            {
                "kind": "t3",
                "data": {
                    "id": "fixturepost2",
                    "title": "TEST FIXTURE: stickied announcement that must be skipped",
                    "permalink": "/r/fixture/comments/fixturepost2/",
                    "created_utc": 1789001000,
                    "score": 10,
                    "stickied": True,
                },
            },
        ]
    },
}


# --------------------------------------------------------------- research fixtures
SOURCE_PAGE_HTML = """<!DOCTYPE html>
<html><head><title>TEST FIXTURE: fabrication capacity report</title>
<script>var tracker = 1;</script><style>body{color:#000}</style></head>
<body>
<h1>Fabrication capacity report</h1>
<p>The regional foundry added 40,000 wafer starts per month in the second quarter,
according to the industry association.</p>
<p>A second association disputes the figure and places the addition nearer 25,000.</p>
<p>Both groups agree that packaging capacity remains the binding constraint.</p>
</body></html>
"""

ROBOTS_ALLOW_ALL = "User-agent: *\nAllow: /\n"
ROBOTS_DISALLOW_ALL = "User-agent: *\nDisallow: /\n"

RESEARCH_RESPONSE = {
    "summary": "Two industry associations report different wafer-start additions; both agree "
    "packaging is the binding constraint.",
    "key_facts": [
        {
            "statement": "Packaging capacity is described as the binding constraint.",
            "classification": "FACT",
            "document_indices": [0],
        }
    ],
    "claims": [
        {
            "statement": "The regional foundry added 40,000 wafer starts per month in Q2.",
            "classification": "CLAIM",
            "attributed_to": "the industry association",
            "document_indices": [0],
        },
        {
            "statement": "A competitor will exit the market next year.",
            "classification": "FACT",
            "document_indices": [],
        },
    ],
    "statistics": [
        {
            "value": "40,000",
            "what_it_measures": "wafer starts added per month",
            "as_of": "2026-Q2",
            "document_indices": [0],
        },
        {
            "value": "83%",
            "what_it_measures": "market share, not present in any document",
            "as_of": None,
            "document_indices": [7],
        },
    ],
    "entities": {"organizations": ["Industry Association"], "people": [], "places": ["region"]},
    "conflicts": [
        {
            "subject": "size of the wafer-start addition",
            "positions": [
                {"position": "about 40,000 per month", "document_indices": [0]},
                {"position": "nearer 25,000 per month", "document_indices": [1]},
            ],
        },
        {
            "subject": "conflict with only one sourced position",
            "positions": [{"position": "only side", "document_indices": [0]}],
        },
    ],
    "uncertainties": ["Whether packaging capacity expands in the next two quarters."],
}

SCRIPT_RESPONSE = {
    "sections": [
        {
            "kind": "hook",
            "heading": "Opening",
            "narration": "Two industry bodies looked at the same quarter and came away with "
            "very different numbers.",
            "document_indices": [0],
        },
        {
            "kind": "evidence",
            "heading": "What the associations say",
            "narration": "One association puts the monthly addition near forty thousand wafer "
            "starts. Another places it closer to twenty-five thousand. Both agree that packaging "
            "is what actually limits output.",
            "document_indices": [0, 1],
        },
        {
            "kind": "conclusion",
            "heading": "Where that leaves us",
            "narration": "Until the two counts are reconciled, the honest answer is that the "
            "scale of the addition is disputed.",
            "document_indices": [0, 99],
        },
    ],
    "notes": "Mention the dispute explicitly rather than averaging the figures.",
}

FACT_CHECK_RESPONSE = {
    "claims": [
        {
            "assertion": "Packaging is the binding constraint on output.",
            "narration_sentence": "Both agree that packaging is what actually limits output.",
            "document_indices": [0],
            "overstated": False,
            "note": None,
        },
        {
            "assertion": "The addition was exactly 40,000 wafer starts.",
            "narration_sentence": "One association puts the monthly addition near forty thousand.",
            "document_indices": [0],
            "overstated": True,
            "note": "The research records this as a disputed claim, not a settled figure.",
        },
        {
            "assertion": "A competitor will exit the market next year.",
            "narration_sentence": "A competitor is leaving the market.",
            "document_indices": [],
            "overstated": False,
            "note": None,
        },
    ],
    "unverified_specifics": ["next year"],
}

FACT_CHECK_ALL_SUPPORTED = {
    "claims": [
        {
            "assertion": "Packaging is the binding constraint on output.",
            "narration_sentence": "Both agree that packaging is what actually limits output.",
            "document_indices": [0],
            "overstated": False,
            "note": None,
        }
    ],
    "unverified_specifics": [],
}
