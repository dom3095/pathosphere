"""
Default source catalogue — 52 sources across 7 geopolitical blocks.

Each entry: (name, url, country, geopolitical_block, orientation, state_control, language, active, notes)

state_control: 0=none  1=partial/subsidy  2=significant  3=full
active:        1=fetch in RSS cycle  0=disabled (no feed or unreliable)
"""

SOURCES: list[tuple] = [
    # ── Wire agencies ─────────────────────────────────────────────────────────
    # DISABLED 2026-06-14: hostname no longer resolves; Reuters dropped public RSS.
    # (
    #     "Reuters",
    #     "https://feeds.reuters.com/reuters/worldNews",
    #     "GB", "western", "independent", 0, "en", 1,
    #     "Legacy URL; Reuters dropped public RSS ~2020 but this still circulates",
    # ),
    (
        "AP News",
        None,
        "US", "western", "independent", 0, "en", 0,
        "No public RSS since 2022; use third-party aggregators",
    ),
    (
        "AFP",
        None,
        "FR", "western", "independent", 1, "en", 0,
        "No public RSS; paid syndication only",
    ),
    (
        "ANSA",
        "https://www.ansa.it/sito/ansait_rss.xml",
        "IT", "western", "state-affiliated", 1, "it", 1,
        "Italian-language general feed; English section has no dedicated RSS",
    ),
    (
        "DPA",
        None,
        "DE", "western", "independent", 0, "de", 0,
        "No public RSS; paid wire only",
    ),
    # DISABLED 2026-06-14: feed returns HTTP 500 / empty; no working EFE feed found.
    # (
    #     "EFE",
    #     "https://www.efe.com/efe/english/4/rss",
    #     "ES", "western", "state-affiliated", 1, "es", 1,
    #     None,
    # ),
    # DISABLED 2026-06-14: RSS 404 on all known paths; no working Kyodo feed found.
    # (
    #     "Kyodo News",
    #     "https://english.kyodonews.net/rss/all.xml",
    #     "JP", "western", "independent", 0, "en", 1,
    #     None,
    # ),
    # DISABLED 2026-06-14: all Xinhua RSS feeds frozen at 2018 (worldrss.xml and
    # english.news.cn identical, stale). Xinhua abandoned RSS; no working feed.
    # State-China voice retained via Global Times. (See China Digital Times /
    # TechNode / SCMP for non-state China coverage.)
    # (
    #     "Xinhua",
    #     "http://www.xinhuanet.com/english/rss/worldrss.xml",
    #     "CN", "china", "state", 3, "en", 1,
    #     "HTTP only (no HTTPS on this domain)",
    # ),
    # DISABLED 2026-06-15: China Daily RSS feeds frozen at 2017 (same issue as
    # Xinhua — feed exists but no new items). No other working state feed found
    # (People's Daily English: 404; Sixth Tone: 404; Caixin: 403).
    # (
    #     "China Daily",
    #     "http://www.chinadaily.com.cn/rss/china_rss.xml",
    #     "CN", "china", "state", 3, "en", 0,
    #     "RSS frozen at 2017-12-12; HTTP only",
    # ),
    (
        "TASS",
        "https://tass.com/rss/v2.xml",
        "RU", "russia", "state", 3, "en", 1,
        None,
    ),
    # DISABLED 2026-06-14: RSS 404 on all known paths; no working ANI feed found.
    # (
    #     "ANI",
    #     "https://www.aninews.in/rss-feed/world/",
    #     "IN", "india", "independent", 0, "en", 1,
    #     None,
    # ),
    (
        "APO Group",
        None,
        "ZA", "africa", "independent", 0, "en", 0,
        "Dynamic RSS via africa-newsroom.com filter; no static public feed",
    ),
    # ── Western editorial ─────────────────────────────────────────────────────
    (
        "BBC World",
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "GB", "western", "public", 1, "en", 1,
        None,
    ),
    (
        "France 24",
        "https://www.france24.com/en/rss",
        "FR", "western", "state-affiliated", 1, "en", 1,
        None,
    ),
    (
        "DW World",
        "https://rss.dw.com/xml/rss-en-world",
        "DE", "western", "state-affiliated", 1, "en", 1,
        None,
    ),
    (
        "MarketWatch",
        "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "US", "western", "independent", 0, "en", 1,
        None,
    ),
    (
        "Financial Times",
        "https://www.ft.com/rss/home",
        "GB", "western", "independent", 0, "en", 1,
        "Headlines only; full text paywalled",
    ),
    (
        "Nikkei Asia",
        "https://asia.nikkei.com/rss/feed/nar",
        "JP", "western", "independent", 0, "en", 1,
        "Personal/non-commercial use per TOS; subscription needed for full text",
    ),
    (
        "The Straits Times",
        "https://www.straitstimes.com/news/world/rss.xml",
        "SG", "western", "independent", 0, "en", 1,
        "May block direct fetch; Singapore neutral-hub perspective",
    ),
    (
        "Haaretz",
        "https://www.haaretz.com/srv/israel-news-rss",
        "IL", "western", "independent", 0, "en", 1,
        "Most articles paywalled; headlines accessible",
    ),
    (
        "OilPrice.com",
        "https://oilprice.com/rss/main",
        "US", "western", "independent", 0, "en", 1,
        "Energy/commodities specialist",
    ),
    (
        "Defense News",
        "https://www.defensenews.com/arc/outboundfeeds/rss/?rss=true",
        "US", "western", "independent", 0, "en", 1,
        "Defense/military specialist",
    ),
    (
        "Taipei Times",
        "https://www.taipeitimes.com/xml/index.rss",
        "TW", "western", "independent", 0, "en", 1,
        None,
    ),
    # DISABLED 2026-06-14: no working RSS found (404 on /rss, /feed, /rss/news.xml).
    # (
    #     "Focus Taiwan (CNA)",
    #     "https://focustaiwan.tw/rss",
    #     "TW", "western", "state-affiliated", 1, "en", 1,
    #     "Central News Agency Taiwan; feed URL uncertain, verify",
    # ),
    (
        "DIGITIMES",
        "https://www.digitimes.com/rss/daily.xml",
        "TW", "western", "independent", 0, "en", 1,
        "Semiconductor/supply-chain specialist; key for semicon pilot",
    ),
    (
        "HK Free Press",
        "https://hongkongfp.com/feed/",
        "HK", "western", "independent", 0, "en", 1,
        "Independent HK journalism; law, politics, national security",
    ),
    # ── China / HK ───────────────────────────────────────────────────────────
    (
        "Global Times",
        "https://www.globaltimes.cn/rss/outbrain.xml",
        "CN", "china", "state", 3, "en", 1,
        None,
    ),
    (
        "South China Morning Post",
        "http://www.scmp.com/rss/91/feed/",
        "HK", "china", "independent", 1, "en", 1,
        "Alibaba-owned; All News feed (was /5 World — higher volume). More nuanced than mainland state media",
    ),
    (
        "South China Morning Post — China",
        "http://www.scmp.com/rss/4/feed/",
        "HK", "china", "independent", 1, "en", 1,
        "SCMP China section (Policies & Politics, Diplomacy, Economy); targeted China coverage",
    ),
    # ── Russia ────────────────────────────────────────────────────────────────
    (
        "RT",
        "https://www.rt.com/rss/news/",
        "RU", "russia", "state", 3, "en", 1,
        "EU-sanctioned: refused directly, fetched via Tor (TOR_SOURCES in rss.py)",
    ),
    # ── Arab / Middle East ────────────────────────────────────────────────────
    (
        "Al Jazeera",
        "https://www.aljazeera.com/xml/rss/all.xml",
        "QA", "arab", "state-affiliated", 2, "en", 1,
        None,
    ),
    (
        "Anadolu Agency",
        "https://www.aa.com.tr/en/rss/default?cat=world",
        "TR", "arab", "state-affiliated", 2, "en", 1,
        "URL updated 2026-06-14 (old ajansgunceleng.xml dead)",
    ),
    (
        "Press TV",
        "https://www.presstv.ir/rss.xml",
        "IR", "arab", "state", 3, "en", 1,
        "URL updated 2026-06-14 (old /rss/rss.xml was 404)",
    ),
    (
        "Arab News",
        "https://www.arabnews.com/rss.xml",
        "SA", "arab", "state-affiliated", 2, "en", 1,
        "Saudi government-aligned; needs browser UA (else 403); URL updated 2026-06-14",
    ),
    # ── India ─────────────────────────────────────────────────────────────────
    (
        "The Hindu",
        "https://www.thehindu.com/news/international/feeder/default.rss",
        "IN", "india", "independent", 0, "en", 1,
        None,
    ),
    # ── Latin America ─────────────────────────────────────────────────────────
    (
        "Folha de S.Paulo",
        "https://feeds.folha.uol.com.br/mundo/rss091.xml",
        "BR", "latam", "independent", 0, "pt", 1,
        None,
    ),
    # ── Africa ────────────────────────────────────────────────────────────────
    (
        "AllAfrica",
        "https://allafrica.com/tools/headlines/rdf/africa/headlines.rdf",
        "ZA", "africa", "independent", 0, "en", 1,
        "Aggregates ~600 stories/day from 90+ African outlets",
    ),
    (
        "Daily Maverick",
        "https://www.dailymaverick.co.za/dmrss",
        "ZA", "africa", "independent", 0, "en", 1,
        None,
    ),
    (
        "RFI Afrique",
        "https://www.rfi.fr/fr/afrique/rss",
        "FR", "western", "state-affiliated", 1, "fr", 1,
        "Radio France Internationale; francophone Africa coverage",
    ),
    (
        "Jeune Afrique",
        "https://www.jeuneafrique.com/feed/",
        "TN", "africa", "independent", 0, "fr", 1,
        "Pan-African; some content paywalled; may return 403",
    ),
    # DISABLED 2026-06-14: HTTP 403/404 on all known paths; Nation Media blocks fetch.
    # (
    #     "The East African",
    #     "https://www.theeastafrican.co.ke/tea/news/feed",
    #     "KE", "africa", "independent", 0, "en", 1,
    #     "Nation Media Group; may block direct fetch",
    # ),
    (
        "Premium Times",
        "https://www.premiumtimesng.com/feed",
        "NG", "africa", "independent", 0, "en", 1,
        "Nigeria; politics, security, economy",
    ),
    (
        "La Nation Djibouti",
        "https://www.lanation.dj/feed",
        "DJ", "africa", "state", 3, "fr", 1,
        "Djibouti = strategic Bab el-Mandeb; Chinese + French military bases",
    ),
    (
        "Somaliland Sun",
        "https://somalilandsun.com/feed",
        "SO", "africa", "independent", 0, "en", 1,
        "Somaliland politics, diplomacy, independence",
    ),
    (
        "Somaliland Standard",
        "https://somalilandstandard.com/feed",
        "SO", "africa", "independent", 0, "en", 1,
        "Horn of Africa, maritime security",
    ),
    # ── South Asia ────────────────────────────────────────────────────────────
    (
        "Dawn",
        "https://www.dawn.com/feeds/home",
        "PK", "other", "independent", 0, "en", 1,
        "Pakistan; most credible English-language outlet",
    ),
    (
        "Geo News",
        "https://www.geo.tv/rss/1/2",
        "PK", "other", "independent", 0, "en", 1,
        "Pakistan; /rss/1/2 = world news",
    ),
    # ── South Caucasus ────────────────────────────────────────────────────────
    # DISABLED 2026-06-14: HTTP 403 (Cloudflare bot block) even with browser UA.
    # (
    #     "Armenpress",
    #     "https://armenpress.am/eng/rss/",
    #     "AM", "other", "state-affiliated", 2, "en", 1,
    #     "Returns 403 on some requests; verify manually",
    # ),
    (
        "EVN Report",
        "https://evnreport.com/feed",
        "AM", "other", "independent", 0, "en", 1,
        "Armenia; analytical; defense, politics, South Caucasus",
    ),
    (
        "Trend News Agency",
        "https://www.trend.az/feeds/index.rss",
        "AZ", "other", "state-affiliated", 2, "en", 1,
        "Azerbaijan; Caucasus, Caspian, Central Asia, energy",
    ),
    (
        "AzerNews",
        "https://www.azernews.az/feed.php",
        "AZ", "other", "state-affiliated", 2, "en", 1,
        None,
    ),
    # ── Added 2026-06-14 (verified via rsscatalog.com): plurality per bloc ─────
    (
        "MERICS",
        "https://merics.org/en/rss",
        "DE", "western", "independent", 1, "en", 1,
        "Mercator Institute for China Studies (Berlin); European China research; partly Bundesministerium-funded",
    ),
    (
        "Taiwan MOFA",
        "https://en.mofa.gov.tw/OpenData.aspx?SN=07564A7F01D47BAD",
        "TW", "western", "state", 2, "en", 1,
        "Taiwan Ministry of Foreign Affairs — News & Events; cross-strait diplomatic signals vs PRC narrative",
    ),
    (
        "The Diplomat",
        "https://thediplomat.com/feed/",
        "US", "western", "independent", 0, "en", 1,
        "Asia-Pacific geopolitics analysis; relevant to Taiwan/semiconductor pilot",
    ),
    (
        "ChinaFile",
        "https://feeds.feedburner.com/chinafile/All",
        "US", "western", "independent", 0, "en", 1,
        "Asia Society; long-form China analysis",
    ),
    (
        "China Digital Times",
        "https://chinadigitaltimes.net/feed/",
        "US", "china", "independent", 0, "en", 1,
        "US-based, critical of Beijing; censorship watch (non-state China voice)",
    ),
    (
        "TechNode",
        "https://technode.com/feed/",
        "CN", "china", "independent", 0, "en", 1,
        "Shanghai-based tech media; China semiconductors/hardware",
    ),
    (
        "The Moscow Times",
        "https://www.themoscowtimes.com/rss/news",
        "RU", "russia", "independent", 0, "en", 1,
        "In exile (Amsterdam); independent Russian voice vs state TASS/RT",
    ),
    (
        "Russia in Global Affairs",
        "https://globalaffairs.ru/feed/",
        "RU", "russia", "establishment", 1, "en", 1,
        "Russian foreign-policy establishment perspective",
    ),
    (
        "NDTV",
        "https://feeds.feedburner.com/NDTV-LatestNews",
        "IN", "india", "independent", 0, "en", 1,
        "Major Indian broadcaster",
    ),
    (
        "Scroll.in",
        "https://feeds.feedburner.com/ScrollinArticles.rss",
        "IN", "india", "independent", 0, "en", 1,
        "Independent Indian digital news",
    ),
]


def seed_sources(conn: "sqlite3.Connection") -> int:  # type: ignore[name-defined]
    """Insert default sources. Returns number of rows inserted."""
    before = conn.total_changes
    conn.executemany(
        """INSERT OR IGNORE INTO sources
           (name, url, country, geopolitical_block, orientation, state_control, language, active, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        SOURCES,
    )
    conn.commit()
    return conn.total_changes - before
