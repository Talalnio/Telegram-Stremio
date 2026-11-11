#----- AIOStreams-compatible stream formatter: same template syntax, fields and presets
import base64
import json
import math
import random
import re

import PTN

from Backend.logger import LOGGER

PRESETS = {
    "torrentio": {
        "name": """{stream.proxied["🕵️‍♂️ "||""]}{stream.private["🔑 "||""]}{stream.type::=p2p["[P2P] "||""]}{service.id::exists["[{service.shortName}"||""]}{service.cached["+] "||" download] "]}{addon.name} {stream.resolution::exists["{stream.resolution}"||"Unknown"]}
{?{stream.visualTags::join(' | ')}?}""",
        "description": """{?ℹ️{stream.message}?}
{?{stream.folderName}?}
{?{stream.filename}?}
{stream.size::>0["💾{stream.size::bytes2} "||""]}{stream.folderSize::>0["/ 💾{stream.folderSize::bytes2}"||""]}{stream.seeders::>=0["👤{stream.seeders} "||""]}{?📅{stream.age} ?}{?⚙️{stream.indexer}?}
{?{stream.languageEmojis::join(' / ')}?}{stream.subtitles::exists::and::stream.languageEmojis::exists[" "||""]}{stream.subtitles::exists["Subs / {stream.subtitleEmojis::join(' / ')}"||""]}
""",
    },
    "torbox": {
        "name": """{stream.proxied["🕵️‍♂️ "||""]}{stream.private["🔑 "||""]}{stream.type::=p2p["[P2P] "||""]}{addon.name}{stream.library[" (Your Media) "||""]}{service.cached[" (Instant "||" ("]}{service.id::exists["{service.shortName})"||""]}{? ({stream.resolution})?}""",
        "description": """Quality: {stream.quality::exists["{stream.quality}"||"Unknown"]}
Name: {stream.filename::exists["{stream.filename}"||"Unknown"]}
Size: {stream.size::>0["{stream.size::bytes} "||""]}{stream.folderSize::>0["/ {stream.folderSize::bytes} "||""]}{?| Source: {stream.indexer} ?}{stream.duration::>0["| Duration: {stream.duration::time} "||""]}
Languages: {?{stream.languages::join(', ')}?}{stream.subtitles::exists::and::stream.languages::exists[" | "||""]}{?Subtitles: {stream.subtitles::join(', ')}?}
{?Message: {stream.message}?}""",
    },
    "gdrive": {
        "name": """{stream.proxied["🕵️ "||""]}{stream.private["🔑 "||""]}{stream.type::=p2p["[P2P] "||""]}{?[{service.shortName}?}{service.cached["⚡] "||"⏳] "]}{addon.name}{stream.library[" (Your Media)"||""]} {?{stream.resolution}?}{stream.seadexBest[" (Best)"||""]}{stream.seadex::and::stream.seadexBest::isfalse[" (SeaDex Alt.)"||""]}{stream.rseMatched::exists::and::stream.seadex::isfalse::and::stream.rseMatched::string::~T1::or::stream.rseMatched::string::~T2::or::stream.rseMatched::string::~T3::or::stream.rseMatched::string::~T4::or::stream.rseMatched::string::~T5::or::stream.rseMatched::string::~T6::or::stream.rseMatched::string::~T7::or::stream.rseMatched::string::~T8[" ({stream.rseMatched::first})"||""]}{stream.regexMatched::exists::and::stream.rseMatched::exists::isfalse::and::stream.seadex::isfalse[" ({stream.regexMatched})"||""]}""",
        "description": """{?🎥 {stream.quality} ?}{?🎞️ {stream.encode} ?}{?🏷️ {stream.releaseGroup} ?}{?📡 {stream.network} ?}{stream.editions::exists["🏆 {stream.editions::join(' | ')} "||""]}
{?📺 {stream.visualTags::join(' | ')} ?}{?🎧 {stream.audioTags::join(' | ')} ?}{?🔊 {stream.audioChannels::join(' | ')}?}
{stream.size::>0["📦 {stream.size::sbytes} "||""]}{stream.folderSize::>0["/ {stream.folderSize::sbytes} "||""]}{stream.bitrate::>0["({stream.bitrate::sbitrate})"||""]}{stream.duration::>0["⏱️ {stream.duration::time} "||""]}{stream.seeders::>0["👥 {stream.seeders} "||""]}{?📅 {stream.age} ?}{?🔍 {stream.indexer}?}
{?🌎 {stream.languages::join(' | ')}?}{?📝 {stream.subtitles::join(' | ')}?}
{stream.filename::exists["📁"||""]} {?{stream.folderName}/?}{?{stream.filename}?}
{?ℹ️ {stream.message}?}
      """,
    },
    "lightgdrive": {
        "name": """{stream.proxied["🕵️ "||""]}{stream.private["🔑 "||""]}{stream.type::=p2p["[P2P] "||""]}{?[{service.shortName}?}{stream.library["☁️"||""]}{service.cached["⚡] "||"⏳] "]}{addon.name}{? {stream.resolution}?}{stream.seadexBest[" (Best)"||""]}{stream.seadex::and::stream.seadexBest::isfalse[" (SeaDex Alt.)"||""]}{stream.rseMatched::exists::and::stream.seadex::isfalse::and::stream.rseMatched::string::~T1::or::stream.rseMatched::string::~T2::or::stream.rseMatched::string::~T3::or::stream.rseMatched::string::~T4::or::stream.rseMatched::string::~T5::or::stream.rseMatched::string::~T6::or::stream.rseMatched::string::~T7::or::stream.rseMatched::string::~T8[" ({stream.rseMatched::first})"||""]}{stream.regexMatched::exists::and::stream.rseMatched::exists::isfalse::and::stream.seadex::isfalse[" ({stream.regexMatched})"||""]}""",
        "description": """{?📁 {stream.title::title}?}{? ({stream.year})?}{? {stream.seasonEpisode::join(' • ')}?}
{?🎥 {stream.quality} ?}{?🎞️ {stream.encode} ?}{?🏷️ {stream.releaseGroup}?}{?📡 {stream.network} ?}{stream.editions::exists[" 🏆 {stream.editions::join(' • ')}"||""]}
{?📺 {stream.visualTags::join(' • ')} ?}{?🎧 {stream.audioTags::join(' • ')} ?}{?🔊 {stream.audioChannels::join(' • ')}?}
{stream.size::>0["📦 {stream.size::sbytes} "||""]}{stream.folderSize::>0["/ {stream.folderSize::sbytes} "||""]}{stream.duration::>0["⏱️ {stream.duration::time} "||""]}{?📅 {stream.age} ?}{?🔍 {stream.indexer}?}
{?🌐 {stream.languageEmojis::join(' / ')}?}{stream.subtitles::exists["📝 {stream.subtitleEmojis::join(' / ')}"||""]}
{?ℹ️ {stream.message}?}""",
    },
    "minimalisticgdrive": {
        "name": """{stream.resolution::exists["{stream.resolution::replace('2160p','✨ 4K')::replace('1440p','📀 2K')::replace('1080p','🧿1080p')::replace('720p','💿720p')}"||"N/A"]}{service.cached[" 🎫 "||" 🎟️ "]}
{?{stream.quality::upper}?}
""",
        "description": """{?🔆 {stream.visualTags::join(' • ')}  ?}{?🔊 {stream.audioTags::join(' • ')}?}
{stream.size::>0["📦 {stream.size::sbytes} "||""]}
{?🌎 {stream.languages::join(' • ')}?}{?📝 {stream.subtitles::join(' • ')}?}
""",
    },
    "prism": {
        "name": """{stream.resolution::exists["{stream.resolution::replace('2160p', '🔥4K UHD')::replace('1440p','✨ QHD')::replace('1080p','🚀 FHD')::replace('720p','💿 HD')::replace('576p','💩 Low Quality')::replace('480p','💩 Low Quality')::replace('360p','💩 Low Quality')::replace('240p','💩 Low Quality')::replace('144p','💩 Low Quality')}"||"💩 Unknown"]}""",
        "description": """{?🎬 {stream.title::title} ?}{?({stream.year}) ?}{?🍂 {stream.formattedSeasons} ?}{?🎞️ {stream.formattedEpisodes}?}{stream.seadexBest["🎚️ Best "||""]}{stream.seadex::and::stream.seadexBest::isfalse["🎚️ Alternative"||""]}{stream.rseMatched::exists::and::stream.seadex::isfalse::and::stream.rseMatched::string::~T1::or::stream.rseMatched::string::~T2::or::stream.rseMatched::string::~T3::or::stream.rseMatched::string::~T4::or::stream.rseMatched::string::~T5::or::stream.rseMatched::string::~T6::or::stream.rseMatched::string::~T7::or::stream.rseMatched::string::~T8[" 🎚️ {stream.rseMatched::first}"||""]}{stream.regexMatched::exists::and::stream.rseMatched::exists::isfalse::and::stream.seadex::isfalse["🎚️ {stream.regexMatched} "||""]}
{?🎥 {stream.quality} ?}{?📺 {stream.visualTags::join(' | ')} ?}{?🎞️ {stream.encode} ?}{stream.duration::>0["⏱️ {stream.duration::time} "||""]}{stream.editions::exists["🏆 {stream.editions::join(' | ')} "||""]}
{?🎧 {stream.audioTags::join(' | ')} ?}{?🔊 {stream.audioChannels::join(' | ')} ?}{stream.languages::exists["🗣️ {stream.languageEmojis::join(' / ')}"||""]}{stream.subtitles::exists["📝 {stream.subtitleEmojis::join(' / ')}"||""]}
{stream.size::>0["📦 {stream.size::sbytes} "||""]}{stream.folderSize::>0["/ {stream.folderSize::sbytes} "||""]}{stream.bitrate::>0["📊 {stream.bitrate::sbitrate} "||""]}{service.cached::isfalse::or::stream.type::=p2p::and::stream.seeders::>0["🌱 {stream.seeders} "||""]}{stream.type::=usenet::and::stream.age::exists["📅 {stream.age} "||""]}
{?🏷️ {stream.releaseGroup} ?}{?📡 {stream.indexer} ?}{?🎭 {stream.network}?}
{service.cached["⚡Ready "||"❌ Not Ready "]}{service.id::exists["({service.shortName}) "||""]}{stream.library["📌 Library "||""]}{stream.type::=Usenet["📰 Usenet "||""]}{stream.type::=p2p["⚠️ P2P "||""]}{stream.type::=http["💻 Web Link "||""]}{stream.type::=youtube["▶️ Youtube "||""]}{stream.type::=live["📺 Live "||""]}{stream.proxied["🔒 Proxied "||""]}{stream.private["🔑 Private "||""]}🔍{addon.name} 
{?ℹ️ {stream.message}?}
""",
    },
    "tamtaro": {
        "name": """{stream.resolution::exists["{stream.resolution::replace('2160p','   4K ')::replace('1440p','    2K ')::replace('p','P')}‍"||"‍     "]}{?‍{stream.type::replace('debrid','    ')::replace('p2p','⁽ᵖ²ᵖ⁾')::replace('live','⁽ˡᶦᵛᵉ⁾')::replace('http','⁽ʷᵉᵇ⁾')::replace('usenet','‍⁽ⁿᶻᵇ⁾‍')::replace('stremio-usenet','‏⁽ⁿᶻᵇ⁾')::replace('info','⁽ᶦⁿᶠᵒ⁾')::replace('statistic','⁽ˢᵗᵃᵗˢ⁾')::replace('external','⁽ᵉˣᵗ⁾')::replace('error','⁽ᵉʳʳᵒʳ⁾')::replace('youtube','⁽ʸᵗ⁾')}‍‍‍?}{service.cached["⚡"||"‍⏳‍​"||""]}{?‍‍
  〈{stream.quality::title::replace('Bluray Remux','Remux')::replace('Web-dl','Web‍-‍dl')::replace('Hc Hd-rip','HC HDRip')::replace('Hdrip','HDRip')}〉‍     ?}{stream.message::~Download["{tools.removeLine}
​"||""]}{?‍
  {stream.nSeScore::star::replace('⯪','☆')}            ?}{stream.message::~Download["{tools.removeLine}
​"||""]}""",
        "description": """{stream.title::exists["{stream.library::istrue["☁︎"||"{stream.preloading::istrue["➤"||"✎"]}"]}  {stream.date::exists["{stream.title::title::truncate(25)}"||"{stream.title::title::truncate(20)}"]}"||""]}{? · {stream.country} ?}{metadata.queryType::~series["{stream.date::exists["
▦  {stream.date::date('%-d %b %Y')}"||""]}"||"{stream.date::exists["▦  {stream.date::date('%-d %b %Y')} "||"{? ({stream.year::replace('-20', '-')::replace('-19', '-')}) ?}"]}"]}{?  {stream.seasonEpisode::join('·')::replace('E','ᴇ')::replace('S','s')::translate('0123456789','₀₁₂₃₄₅₆₇₈₉')}?}
{?▣  {stream.encode} ?}{stream.visualTags::exists["{stream.visualTags::in('DV','HLG','HDR','HDR10','HDR10+')["✦ "||"✧ "]}{stream.visualTags::sort::join(' · ')::replace('HDR · HDR','HDR')::replace('HDR10 · HDR10','HDR10')}  "||""]}{stream.visualTags::sort::join(' · ')::replace('HDR · HDR','HDR')::replace('HDR10 · HDR10','HDR10')::length::<=6::and::stream.audioTags::lsort::join(' · ')::replace('DD · DD','DD')::replace('DTS · DTS','DTS')::length::<=10::and::stream.audioChannels::length::<=2["{?♬ {stream.audioTags::lsort::join(' · ')::replace('DD · DD','DD')::replace('DTS · DTS','DTS')}?}{?  ♯ {stream.audioChannels::rsort::join(' · ')}?}"||""]}
{stream.visualTags::sort::join(' · ')::replace('HDR · HDR','HDR')::replace('HDR10 · HDR10','HDR10')::length::>6::or::stream.audioTags::lsort::join(' · ')::replace('DD · DD','DD')::replace('DTS · DTS','DTS')::length::>10::or::stream.audioTags::lsort::join(' · ')::replace('DD · DD','DD')::replace('DTS · DTS','DTS')::length::>10::or::stream.audioChannels::length::>2["{?♬  {stream.audioTags::lsort::join(' · ')::replace('DD · DD','DD')::replace('DTS · DTS','DTS')}?}{?  ♯ {stream.audioChannels::rsort::join(' · ')}?}"||""]}
{stream.size::>0["{stream.seasonPack["❖ "||"◈ "||""]}{stream.size::>0::and::stream.folderSize::>0::and::stream.size::sbytes::~GB::and::stream.folderSize::sbytes::~GB["{stream.size::sbytes::replace(' GB','')}"||"{stream.size::sbytes}"]}"||""]}{stream.folderSize::>0[" / {stream.folderSize::sbytes}"||""]}{? · {stream.bitrate::sbitrate::replace('Mbps','ᴹᵇᵖˢ')::replace('Kbps','ᴷᵇᵖˢ')}?}{stream.message::~Download["{tools.removeLine}"||""]}{service.cached::isfalse::or::stream.type::=p2p::and::stream.seeders::>0["⇄ {stream.seeders}❦"||""]}{? · {stream.age}?}
{stream.proxied["⛊ "||"⛉ "]}{?[{service.shortName}] ?}{addon.name}{stream.private[" ⚿ ᴘʀɪᴠᴀᴛᴇ "||""]}{? · {stream.releaseGroup::truncate(13)}?}{stream.indexer::exists[" · {stream.indexer::truncate(13)}"||""]}{stream.message::~Download["{tools.removeLine}
"||""]}
{?{stream.audioTracks::where('lang={user.languages}')::exists::or::stream.uSmallLanguageCodes::exists::or::stream.subbed::or::stream.subtitleTracks::where('lang={user.subtitles}')::exists::or::stream.uSmallSubtitleCodes::exists["{stream.mediaInfoQuality::exists["✓"||"⛿"]}"||""]}{stream.audioTracks::where('lang={user.languages}')::exists[" {stream.audioTracks::where('lang={user.languages}')::each("{track.lang::languageCode::smallcaps::replace('ꜱ','s')}{track.title::~descriptive::or::track.visualImpaired["ᵈᵉˢᶜʳⁱᵇᵉᵈ"||""]}{track.commentary["ᶜᵒᵐᵐ"||""]}")::unique::join(' · ')}"||"{stream.uSmallLanguageCodes::exists[" {stream.uSmallLanguageCodes::join(' · ')::replace('ꜱ','s')::replace('ᴅᴜᴀʟ ᴀᴜᴅɪᴏ','ᴅᴜᴏ')::replace('ᴅᴜʙʙᴇᴅ','ᴅᴜʙ')}"||""]}"]}{stream.subbed["{stream.uLanguages::exists[" · sᴜʙ "||" sᴜʙ "]}"||""]}{stream.subtitleTracks::where('lang={user.subtitles}')::exists["({stream.subtitleTracks::where('lang={user.subtitles}')::each("{track.lang::languageCode::smallcaps::replace('ꜱ','s')}{track.title::~dubtitle::or::track.title::~cc["{metadata.queryType::~anime["ᵈᵘᵇ"||""]}"||""]}{track.title::~sign::or::track.forced::or::track.title::~forced["ᶠᵒʳᶜᵉᵈ"||""]}{track.title::~cc::or::track.hearingImpaired::or::track.title::~sdh["{metadata.queryType::~anime::isfalse["ᶜᶜ"||""]}"||""]}")::unique::join(' · ')})"||"{stream.uSmallSubtitleCodes::exists["({stream.uSmallSubtitleCodes::join(' · ')::replace('ꜱ','s')})"||""]}"]} ?}
{stream.message::length::>0::or::stream.rseMatched::length::>0::or::stream.editions::exists::or::stream.network::exists::or::stream.seScore::>0::or::stream.seScore::<0[" » "||""]}{stream.seadex["{stream.seadexBest[" ʙᴇsᴛ ʀᴇʟᴇᴀsᴇ "||" ᴀʟᴛ ʙᴇsᴛ ʀᴇʟᴇᴀsᴇ "]}"||""]}{stream.rseMatched::length::>0["{stream.rseMatched::remove('TrueHD ATMOS','DD+ ATMOS','ATMOS','TrueHD','DTS-HD MA','FLAC','DTS-HD HRA','DD+','DD','DTS-ES','DTS X','DTS','AAC','Opus','DV (Disk)','DV','HDR10+ Boost','HDR','IMAX Enhanced','IMAX','INTERNAL','No-RlsGroup','FHD','HD','4K','126811','Season Pack')::join(' ')::replace('UHD ','')::replace('HD ','')::replace('Movies Anywhere','MA')::replace('Streaming Boost','')::translate('0123456789','₀₁₂₃₄₅₆₇₈₉')::smallcaps::replace('ꜱ','s')} "||""]}{stream.rseMatched::length::>0::isfalse::and::stream.network::exists["{stream.network::smallcaps::replace('ꜱ','s')} "||""]}{stream.rseMatched::length::>0::isfalse::and::stream.network::exists::isfalse::and::stream.editions::exists["{stream.editions::join('  ')::remove('Edition')::smallcaps::replace('ꜱ','s')} "||""]}{stream.message::length::>0["{stream.message::replace('NZB Health: ✅','✅ ɴᴢʙ')::replace('NZB Health: 🧝','🧝 ɴᴢʙ')::replace('AvailNZB 💚','💚 ɴᴢʙ')::replace('NZB Health: ⚠️','ᴜɴᴠᴇʀɪғɪᴇᴅ ɴᴢʙ')::replace('NZB Health: 🚫','✘ɴᴢʙ')::smallcaps::replace('ꜱ','s')} "||""]}{stream.seScore::>0::or::stream.seScore::<0["{stream.seScore::string::translate('0123456789','₀₁₂₃₄₅₆₇₈₉')}"||""]}{stream.message::~Download["{tools.removeLine}"||""]}{service.cached::istrue::and::stream.message::~Download::istrue["
➥ DL Stream"||""]}""",
    },
}

FIELDS = {
    "config": ["addonName"],
    "stream": [
        "filename", "folderName", "size", "bitrate", "folderSize", "library", "quality", "resolution", "subbed",
        "dubbed", "languages", "mediaInfoQuality", "uLanguages", "subtitles", "uSubtitles", "languageEmojis",
        "uLanguageEmojis", "subtitleEmojis", "uSubtitleEmojis", "languageCodes", "uLanguageCodes", "subtitleCodes",
        "uSubtitleCodes", "smallLanguageCodes", "uSmallLanguageCodes", "smallSubtitleCodes", "uSmallSubtitleCodes",
        "wedontknowwhatakilometeris", "uWedontknowwhatakilometeris", "visualTags", "audioTags", "audioTracks",
        "subtitleTracks", "releaseGroup", "regexMatched", "rankedRegexMatched", "regexScore", "nRegexScore",
        "encode", "audioChannels", "edition", "editions", "remastered", "regraded", "repack", "proper",
        "uncensored", "unrated", "upscaled", "hasChapters", "network", "site", "container", "extension",
        "indexer", "year", "title", "country", "episodeTitle", "date", "folderSeasons", "formattedFolderSeasons",
        "seasons", "season", "formattedSeasons", "episodes", "episode", "formattedEpisodes", "folderEpisodes",
        "formattedFolderEpisodes", "seasonEpisode", "seasonPack", "seeders", "private", "freeleech", "age",
        "ageHours", "duration", "infoHash", "type", "message", "proxied", "seadex", "seadexBest", "seScore",
        "nSeScore", "seMatched", "rseMatched", "preloading", "idMatched",
        "channel", "parts",
    ],
    "metadata": [
        "queryType", "type", "isAnime", "title", "titles", "year", "yearEnd", "runtime", "episodeRuntime",
        "genres", "originalLanguage", "country", "season", "episode", "absoluteEpisode",
        "relativeAbsoluteEpisode", "episodeTitle", "episodeTitles", "latestSeason", "daysSinceRelease",
        "daysSinceFirstAired", "daysSinceLastAired", "hasNextEpisode", "daysUntilNextEpisode", "anilistId",
        "malId", "hasSeaDex",
    ],
    "user": [
        "languages", "subtitles", "resolutions", "qualities", "visualTags", "audioTags", "audioChannels",
        "encodes", "streamTypes", "releaseGroups", "keywords",
    ],
    "track": [
        "lang", "codec", "tag", "tags", "channels", "title", "default", "forced", "commentary", "dub",
        "original", "hearingImpaired", "visualImpaired",
    ],
    "service": ["id", "shortName", "name", "cached"],
    "addon": ["name", "presetId", "manifestUrl"],
    "debug": ["json", "jsonf"],
}
_CANON = {f"{s}.{p}".lower(): (s, p) for s, props in FIELDS.items() for p in props}

NL, RL = "\x11", "\x12"
MAX_LEN = 8000
MAX_DEPTH = 5
_ABSENT = object()


#----- Template parser (single pass; unparseable spans stay literal text)
COMPARATORS = ["and", "or", "xor", "neq", "equal", "left", "right"]
PREFIX_OPS = [">=", "<=", "$", "^", "~", "=", ">", "<"]
CALLS = [
    ("replace", "replaceArgs"), ("remove", "loose"), ("keep", "loose"), ("join", "quoted"),
    ("truncate", "digits"), ("slice", "digitsOrPair"), ("time", "quoted"), ("date", "quoted"),
    ("default", "quoted"), ("in", "loose"), ("translate", "quotedPair"), ("trim", "quoted"),
    ("where", "quotedList"), ("pluck", "quoted"), ("each", "template"),
]
STRING_MODS = ["upper", "lower", "trim", "title", "length", "reverse", "base64", "string", "smallcaps",
               "subscript", "superscript", "languagecode", "languageemoji"]
NUMBER_MODS = ["comma", "hex", "octal", "binary", "bytes", "sbytes", "sbytes10", "sbytes2", "rbytes", "bytes10",
               "rbytes10", "bytes2", "rbytes2", "bitrate", "rbitrate", "sbitrate", "string", "time", "star", "pstar"]
ARRAY_MODS = ["join", "length", "first", "last", "random", "sort", "rsort", "lsort", "reverse", "unique",
              "languagecode", "languageemoji", "string"]
EXACT_CONDS = ["istrue", "isfalse", "exists"]
PLAIN_MODS = sorted(STRING_MODS + ["string"] + NUMBER_MODS + ARRAY_MODS + EXACT_CONDS, key=len, reverse=True)
_IDENT = re.compile(r"[A-Za-z0-9_]")
_LOOKS_LIKE_EXPR = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*\.\s*[A-Za-z_][A-Za-z0-9_]*")
_LOOSE_REF = re.compile(r"^\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\.\s*[A-Za-z_][A-Za-z0-9_]*\s*\}$")


class _Scanner:
    def __init__(self, text):
        self.s, self.pos = text, 0

    @property
    def end(self):
        return self.pos >= len(self.s)

    def peek(self, off=0):
        i = self.pos + off
        return self.s[i] if i < len(self.s) else None

    def starts(self, lit):
        return self.s[self.pos:self.pos + len(lit)].lower() == lit.lower()

    def eat(self, lit):
        if self.starts(lit):
            self.pos += len(lit)
            return True
        return False

    def spaces(self):
        while self.peek() is not None and self.peek().isspace():
            self.pos += 1


def _is_ident(c):
    return c is not None and bool(_IDENT.match(c))


def _scan_quoted(sc):
    q = sc.peek()
    if q not in ("'", '"'):
        return False
    sc.pos += 1
    while not sc.end:
        if sc.peek() == q:
            after = sc.peek(1)
            if after is None or after in ",)" or after.isspace():
                sc.pos += 1
                return True
        sc.pos += 1
    return False


def scan_template_arg(text, start):
    q = text[start] if start < len(text) else None
    if q not in ('"', "'"):
        return None
    out, depth, pos = [], 0, start + 1
    while pos < len(text):
        c = text[pos]
        if c == "\\" and text[pos + 1:pos + 2] == q:
            out.append(q)
            pos += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth = max(0, depth - 1)
        elif c == q and depth == 0:
            return "".join(out), pos + 1
        out.append(c)
        pos += 1
    return None


def each_template(source):
    if not source.lower().startswith("each("):
        return None
    start = 5
    while start < len(source) and source[start].isspace():
        start += 1
    found = scan_template_arg(source, start)
    return found[0] if found else None


def _scan_digits(sc):
    start = sc.pos
    while sc.peek() is not None and sc.peek().isdigit():
        sc.pos += 1
    return sc.pos > start


def _scan_loose(sc):
    last = -1
    while not sc.end:
        c = sc.peek()
        if c in ("'", '"'):
            close = sc.s.find(c, sc.pos + 1)
            if close != -1:
                sc.pos = close + 1
                continue
        if c == "{":
            close = sc.s.find("}", sc.pos)
            span = "" if close == -1 else sc.s[sc.pos:close + 1]
            if not _LOOSE_REF.match(span):
                break
            sc.pos = close + 1
            continue
        if c in "}[]" or (c == ":" and sc.peek(1) == ":"):
            break
        if c == ")":
            last = sc.pos
        sc.pos += 1
    if last == -1:
        return False
    sc.pos = last
    return True


def _scan_call(sc, shape):
    if not sc.eat("("):
        return False
    if shape == "quoted":
        if not _scan_quoted(sc):
            return False
    elif shape == "quotedPair":
        if not _scan_quoted(sc):
            return False
        sc.spaces()
        if not sc.eat(","):
            return False
        sc.spaces()
        if not _scan_quoted(sc):
            return False
    elif shape == "quotedList":
        while True:
            sc.spaces()
            if not _scan_quoted(sc):
                return False
            sc.spaces()
            if not sc.eat(","):
                break
    elif shape == "template":
        sc.spaces()
        found = scan_template_arg(sc.s, sc.pos)
        if not found:
            return False
        sc.pos = found[1]
        sc.spaces()
    elif shape == "replaceArgs":
        if sc.peek() == "{":
            while not sc.end and sc.peek() != "}":
                sc.pos += 1
            if not sc.eat("}"):
                return False
        elif not _scan_quoted(sc):
            return False
        sc.spaces()
        if not sc.eat(","):
            return False
        sc.spaces()
        if not _scan_quoted(sc):
            return False
    elif shape == "digits":
        if not _scan_digits(sc):
            return False
    elif shape == "digitsOrPair":
        sc.spaces()
        if not _scan_digits(sc):
            return False
        sc.spaces()
        if sc.eat(","):
            sc.spaces()
            if not _scan_digits(sc):
                return False
            sc.spaces()
    elif shape == "loose":
        return _scan_loose(sc) and sc.eat(")")
    return sc.eat(")")


def _parse_modifier(sc):
    start = sc.pos
    for name, shape in CALLS:
        if not sc.starts(name + "("):
            continue
        sc.pos += len(name)
        if _scan_call(sc, shape):
            return sc.s[start:sc.pos]
        sc.pos = start
        break
    for op in PREFIX_OPS:
        if sc.starts(op):
            sc.pos += len(op)
            while not sc.end:
                c = sc.peek()
                if c in "}[]" or (c == ":" and sc.peek(1) == ":"):
                    break
                sc.pos += 1
            return sc.s[start:sc.pos]
    for name in PLAIN_MODS:
        if sc.starts(name) and not _is_ident(sc.peek(len(name))):
            sc.pos += len(name)
            return sc.s[start:sc.pos]
    sc.pos = start
    return None


def _parse_operand(sc):
    if sc.peek() in ("'", '"'):
        q, start = sc.peek(), sc.pos
        sc.pos += 1
        frm = sc.pos
        while not sc.end and sc.peek() != q:
            sc.pos += 1
        if sc.end:
            sc.pos = start
            return None
        node = {"literal": sc.s[frm:sc.pos], "section": "", "property": ""}
        sc.pos += 1
    else:
        start = sc.pos
        while _is_ident(sc.peek()):
            sc.pos += 1
        section = sc.s[start:sc.pos]
        if not section or sc.peek() != ".":
            sc.pos = start
            return None
        sc.pos += 1
        pstart = sc.pos
        while _is_ident(sc.peek()):
            sc.pos += 1
        canon = _CANON.get(f"{section}.{sc.s[pstart:sc.pos]}".lower())
        if not canon:
            sc.pos = start
            return None
        node = {"literal": None, "section": canon[0], "property": canon[1]}
    mods = []
    while sc.starts("::"):
        save = sc.pos
        sc.pos += 2
        if any(sc.starts(c + "::") for c in COMPARATORS):
            sc.pos = save
            break
        mod = _parse_modifier(sc)
        if mod is None:
            sc.pos = save
            break
        mods.append(mod)
    node["modifiers"] = mods
    return node


def _parse_check(sc):
    start = sc.pos
    if not sc.eat("["):
        return None

    def branch():
        if not sc.eat('"'):
            return None
        out, depth = [], 0
        while not sc.end:
            c = sc.peek()
            if c == "\\" and sc.peek(1) == '"':
                out.append('"')
                sc.pos += 2
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth = max(0, depth - 1)
            elif c == '"' and depth == 0:
                sc.pos += 1
                return "".join(out)
            out.append(c)
            sc.pos += 1
        return None

    t = branch()
    f = sc.eat("||") and branch() if t is not None else None
    if t is None or f is None:
        sc.pos = start
        return None
    a = None
    if sc.starts("||"):
        sc.pos += 2
        a = branch()
        if a is None:
            sc.pos = start
            return None
    if not sc.eat("]"):
        sc.pos = start
        return None
    return t, f, a


def _parse_group_body(sc):
    start = sc.pos
    if not sc.eat("{?"):
        return None
    frm, depth = sc.pos, 1
    while not sc.end:
        if sc.starts("{?"):
            depth += 1
            sc.pos += 2
            continue
        if sc.starts("?}"):
            depth -= 1
            if depth == 0:
                body = sc.s[frm:sc.pos]
                sc.pos += 2
                return body
            sc.pos += 2
            continue
        sc.pos += 1
    sc.pos = start
    return None


def _parse_expression(sc):
    start = sc.pos
    if not sc.eat("{"):
        return None
    sc.spaces()
    first = _parse_operand(sc)
    if not first:
        sc.pos = start
        return None
    operands, comps = [first], []
    while sc.starts("::"):
        save = sc.pos
        sc.pos += 2
        comp = next((c for c in COMPARATORS if sc.starts(c + "::")), None)
        if not comp:
            sc.pos = save
            break
        sc.pos += len(comp) + 2
        op = _parse_operand(sc)
        if not op:
            sc.pos = start
            return None
        comps.append(comp)
        operands.append(op)
    check = _parse_check(sc) if sc.peek() == "[" else None
    sc.spaces()
    if not sc.eat("}"):
        sc.pos = start
        return None
    return {"kind": "expr", "operands": operands, "comparators": comps, "check": check}


def parse_template(text):
    sc, nodes, lit = _Scanner(text), [], 0

    def flush(end):
        if end > lit:
            nodes.append({"kind": "raw", "text": text[lit:end]})

    while not sc.end:
        if sc.peek() != "{":
            sc.pos += 1
            continue
        brace = sc.pos
        if sc.starts("{?"):
            body = _parse_group_body(sc)
            if body is not None:
                flush(brace)
                nodes.append({"kind": "group", "nodes": parse_template(body)})
                lit = sc.pos
                continue
        node = None
        for tool in ("newLine", "removeLine"):
            if sc.eat("{tools." + tool + "}"):
                node = {"kind": "tool", "tool": tool}
                break
        node = node or _parse_expression(sc)
        if not node:
            closing = text.find("}", brace)
            inner = "" if closing == -1 else text[brace + 1:closing]
            if "{" not in inner and _LOOKS_LIKE_EXPR.match(inner):
                flush(brace)
                nodes.append({"kind": "raw", "text": f"{{invalid_expression({inner.strip()})}}"})
                sc.pos = lit = closing + 1
                continue
            sc.pos = brace + 1
            continue
        flush(brace)
        nodes.append(node)
        lit = sc.pos
    flush(len(text))
    return nodes


#----- Value helpers (mirror JavaScript semantics the templates were written for)
def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _js_str(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return repr(v)
    if isinstance(v, list):
        return ",".join(_js_str(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    return str(v)


def _js_number(s):
    s = s.strip()
    if s == "":
        return 0.0
    return float(s) if re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?", s, re.I) else math.nan


def _truthy(v):
    if isinstance(v, (list, dict)):
        return True
    if _is_num(v):
        return v != 0 and not (isinstance(v, float) and math.isnan(v))
    return bool(v)


def is_present(v):
    if v is None or v is _ABSENT:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, list):
        return len(v) > 0
    return True


def _is_obj_list(v):
    return isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict)


def _sanitise(s):
    return s.replace(NL, "").replace(RL, "")


def _subst_tools(s):
    return s.replace("{tools.newLine}", NL).replace("{tools.removeLine}", RL)


def _num_text(v):
    return str(int(v)) if float(v).is_integer() else repr(round(v, 10))


def _to_fixed(v, n):
    return float(f"{v:.{n}f}")


def format_bytes(b, k, rnd=False):
    if b == 0:
        return "0 B"
    sizes = ["B", "KiB", "MiB", "GiB", "TiB"] if k == 1024 else ["B", "KB", "MB", "GB", "TB"]
    i = min(len(sizes) - 1, max(0, math.floor(math.log(b) / math.log(k))))
    v = _to_fixed(b / k ** i, 2)
    return f"{_num_text(round(v) if rnd else v)} {sizes[i]}"


def _smart(raw):
    ip = math.floor(raw)
    if ip >= 100:
        return str(round(raw))
    if ip >= 10:
        v = _to_fixed(raw, 1)
        return f"{v:.0f}" if v % 1 == 0 else f"{v:.1f}"
    return _num_text(_to_fixed(raw, 2))


def format_smart_bytes(b, k):
    if b == 0:
        return "0 B"
    sizes = ["B", "KiB", "MiB", "GiB", "TiB"] if k == 1024 else ["B", "KB", "MB", "GB", "TB"]
    i = min(len(sizes) - 1, max(0, math.floor(math.log(b) / math.log(k))))
    return f"{_smart(b / k ** i)} {sizes[i]}"


def _bitrate(b, smart=False, rnd=False):
    if not math.isfinite(b) or b <= 0:
        return "0 bps"
    sizes = ["bps", "Kbps", "Mbps", "Gbps", "Tbps"]
    i = min(4, max(0, math.floor(math.log(b) / math.log(1000))))
    raw = b / 1000 ** i
    if smart:
        return f"{_smart(raw)} {sizes[i]}"
    return f"{_num_text(round(raw) if rnd else _to_fixed(raw, 2))} {sizes[i]}"


def _norm_duration(d):
    return 0 if d < 0 else d * 60000 if d < 1000 else d


def format_duration(ms):
    s = math.floor(ms / 1000)
    m, h = s // 60, s // 3600
    if h > 0:
        return f"{h}h:{m % 60}m:{s % 60}s"
    return f"{m % 60}m:{s % 60}s" if s % 60 > 0 else f"{m % 60}m"


def _render_pattern(pattern, resolve):
    stack = [{"text": "", "zero": True, "saw": False}]

    def close():
        g = stack.pop()
        p = stack[-1]
        if not g["saw"] or not g["zero"]:
            p["text"] += g["text"]
            p["saw"] = p["saw"] or g["saw"]
            if not g["zero"]:
                p["zero"] = False

    i = 0
    while i < len(pattern):
        c, top = pattern[i], stack[-1]
        if c == "%":
            nxt = pattern[i + 1] if i + 1 < len(pattern) else None
            if nxt is None:
                top["text"] += "%"
                break
            if nxt in "%[]":
                top["text"] += nxt
                i += 2
                continue
            tok = pattern[i + 1:i + 3] if nxt == "-" else nxt
            res = resolve(tok)
            if res is None:
                top["text"] += "%" + tok
            else:
                top["text"] += res[0]
                top["saw"] = True
                if not res[1]:
                    top["zero"] = False
            i += 1 + len(tok)
            continue
        if c == "[":
            stack.append({"text": "", "zero": True, "saw": False})
        elif c == "]" and len(stack) > 1:
            close()
        else:
            top["text"] += c
        i += 1
    while len(stack) > 1:
        close()
    return stack[0]["text"]


def format_duration_pattern(ms, pattern):
    units = set()

    def probe(tok):
        u = tok.lstrip("-")
        if u not in ("H", "M", "S"):
            return None
        units.add(u)
        return ("", True)

    _render_pattern(pattern, probe)
    total = max(0, math.floor(ms / 1000))
    vals = {"H": total // 3600, "M": (total // 60) % 60 if "H" in units else total // 60,
            "S": total % 60 if units & {"H", "M"} else total}

    def resolve(tok):
        padded = not tok.startswith("-")
        v = vals.get(tok if padded else tok[1:])
        if v is None:
            return None
        return (f"{v:02d}" if padded else str(v), v == 0)

    return _render_pattern(pattern, resolve)


_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]
_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def format_date_pattern(value, pattern):
    import datetime
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", value.strip())
    if not m:
        return value
    try:
        d = datetime.date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return value
    day = d.day
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    toks = {"Y": str(d.year), "y": f"{d.year % 100:02d}", "m": f"{d.month:02d}", "-m": str(d.month),
            "d": f"{day:02d}", "-d": str(day), "o": f"{day}{suffix}", "B": _MONTHS[d.month - 1],
            "b": _MONTHS[d.month - 1][:3], "A": _DAYS[d.weekday()], "a": _DAYS[d.weekday()][:3]}
    return _render_pattern(pattern, lambda t: (toks[t], False) if t in toks else None)


_SMALL = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"))


def make_small(s):
    return "".join(_SMALL.get(c.upper(), c) for c in s)


def _map_chars(s, frm, to):
    table = dict(zip(frm, to))
    return "".join(table.get(c, c) for c in s)


#----- Languages: (code, emoji, filename pattern)
LANGUAGES = {
    "Multi": ("", "🌎", r"multi"),
    "Dual Audio": ("", "", r"dual[ .\-_]?(audio|lang(uage)?|flac|ac3|aac2?)"),
    "Dubbed": ("", "", r"dub(s|bed|bing)?"),
    "English": ("EN", "🇬🇧", r"english|eng"),
    "Japanese": ("JA", "🇯🇵", r"japanese|jap|jpn"),
    "Chinese": ("ZH", "🇨🇳", r"chinese|chi"),
    "Russian": ("RU", "🇷🇺", r"russian|rus"),
    "Arabic": ("AR", "🇸🇦", r"arabic|ara"),
    "Portuguese": ("PT", "🇵🇹", r"portuguese"),
    "Portuguese (Brazil)": ("PT-BR", "🇧🇷", r"portuguese[ .\-_]?brazil|pt[ .\-_]?br"),
    "Spanish": ("ES", "🇪🇸", r"spanish|spa|esp"),
    "French": ("FR", "🇫🇷", r"french|fra|fr|vf|vff|vfi|vf2|vfq|truefrench"),
    "German": ("DE", "🇩🇪", r"deu(tsch)?(land)?|ger(man)?"),
    "Italian": ("IT", "🇮🇹", r"italian|ita"),
    "Korean": ("KO", "🇰🇷", r"korean|kor"),
    "Hindi": ("HI", "🇮🇳", r"hindi|hin"),
    "Bengali": ("BN", "🇧🇩", r"bengali|ben(?![ .\-_]?the[ .\-_]?men)"),
    "Punjabi": ("PA", "🇵🇰", r"punjabi|pan"),
    "Marathi": ("MR", "🇮🇳", r"marathi|mar"),
    "Gujarati": ("GU", "🇮🇳", r"gujarati|guj"),
    "Tamil": ("TA", "🇮🇳", r"tamil|tam"),
    "Telugu": ("TE", "🇮🇳", r"telugu|tel"),
    "Kannada": ("KN", "🇮🇳", r"kannada|kan"),
    "Malayalam": ("ML", "🇮🇳", r"malayalam|mal"),
    "Thai": ("TH", "🇹🇭", r"thai|tha"),
    "Vietnamese": ("VI", "🇻🇳", r"vietnamese|vie"),
    "Indonesian": ("ID", "🇮🇩", r"indonesian|ind"),
    "Turkish": ("TR", "🇹🇷", r"turkish|tur"),
    "Hebrew": ("HE", "🇮🇱", r"hebrew|heb"),
    "Persian": ("FA", "🇮🇷", r"persian|per"),
    "Ukrainian": ("UK", "🇺🇦", r"ukrainian|ukr"),
    "Greek": ("EL", "🇬🇷", r"greek|ell"),
    "Lithuanian": ("LT", "🇱🇹", r"lithuanian|lit"),
    "Latvian": ("LV", "🇱🇻", r"latvian|lav"),
    "Estonian": ("ET", "🇪🇪", r"estonian|est"),
    "Polish": ("PL", "🇵🇱", r"polish|pol"),
    "Czech": ("CS", "🇨🇿", r"czech|cze"),
    "Slovak": ("SK", "🇸🇰", r"slovak|slo"),
    "Hungarian": ("HU", "🇭🇺", r"hungarian|hun"),
    "Romanian": ("RO", "🇷🇴", r"romanian|rum"),
    "Bulgarian": ("BG", "🇧🇬", r"bulgarian|bul"),
    "Serbian": ("SR", "🇷🇸", r"serbian|srp"),
    "Croatian": ("HR", "🇭🇷", r"croatian|hrv"),
    "Slovenian": ("SL", "🇸🇮", r"slovenian|slv"),
    "Dutch": ("NL", "🇳🇱", r"dutch|dut"),
    "Danish": ("DA", "🇩🇰", r"danish|dan"),
    "Finnish": ("FI", "🇫🇮", r"finnish|fin"),
    "Swedish": ("SV", "🇸🇪", r"swedish|swe"),
    "Norwegian": ("NO", "🇳🇴", r"norwegian|nor"),
    "Malay": ("MS", "🇲🇾", r"malay"),
    "Latino": ("ES-MX", "💃🏻", r"latino|lat"),
}
_LANG_LOOKUP = {}
for _name, (_code, _, _pat) in LANGUAGES.items():
    _LANG_LOOKUP[_name.lower()] = _name
    if _code:
        _LANG_LOOKUP.setdefault(_code.lower(), _name)
    for _alt in _pat.split("|"):
        if re.fullmatch(r"[a-z]{2,3}", _alt):
            _LANG_LOOKUP.setdefault(_alt, _name)


def normalise_language(v):
    return _LANG_LOOKUP.get(str(v).strip().lower())


def language_code(v):
    name = normalise_language(v) or v
    return (LANGUAGES.get(name) or ("",))[0] or name.upper()


def language_emoji(v):
    name = normalise_language(v) or v
    return (LANGUAGES.get(name) or ("", ""))[1]


def _map_langs(values, fn):
    out = []
    for x in values:
        y = fn(str(x))
        if y and y not in out:
            out.append(y)
    return out


#----- Modifiers
def _natural_key(v):
    return [(0, int(t), "") if t.isdigit() else (1, 0, t.lower()) for t in re.split(r"(\d+)", _js_str(v)) if t]


def _stars(v, pad):
    full, half = math.floor(v / 20), 1 if v % 20 >= 10 else 0
    return "★" * full + "⯪" * half + ("☆" * (5 - full - half) if pad else "")


def _js_comma(v):
    if isinstance(v, float) and not v.is_integer():
        return f"{v:,.3f}".rstrip("0").rstrip(".")
    return f"{int(v):,}"


def _js_radix(v, fmt):
    return format(int(v), fmt) if float(v).is_integer() else _js_str(v)


def _unique(v):
    seen, out = set(), []
    for x in v:
        k = _js_str(x).lower()
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _title(s):
    return " ".join(w.lower()[:1].upper() + w.lower()[1:] for w in s.split(" "))


_STRING_FN = {
    "upper": str.upper, "lower": str.lower, "trim": str.strip, "title": _title,
    "length": lambda s: str(len(s)), "reverse": lambda s: s[::-1],
    "base64": lambda s: base64.b64encode(s.encode()).decode(), "string": lambda s: s,
    "smallcaps": make_small, "subscript": lambda s: _map_chars(s, "0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎"),
    "superscript": lambda s: _map_chars(s, "0123456789+-=()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾"),
    "languagecode": language_code, "languageemoji": language_emoji,
}
_ARRAY_FN = {
    "join": lambda v: ", ".join(_js_str(x) for x in v), "length": lambda v: str(len(v)),
    "first": lambda v: _js_str(v[0]) if v else "", "last": lambda v: _js_str(v[-1]) if v else "",
    "random": lambda v: _js_str(random.choice(v)) if v else "",
    "sort": lambda v: sorted(v, key=lambda x: (0, x, []) if _is_num(x) else (1, 0, _natural_key(x))),
    "rsort": lambda v: sorted(v, key=lambda x: (0, x, []) if _is_num(x) else (1, 0, _natural_key(x)), reverse=True),
    "lsort": lambda v: sorted(v, key=_js_str), "reverse": lambda v: v[::-1], "unique": _unique,
    "languagecode": lambda v: _map_langs(v, language_code), "languageemoji": lambda v: _map_langs(v, language_emoji),
    "string": _js_str,
}
_NUMBER_FN = {
    "comma": _js_comma, "hex": lambda v: _js_radix(v, "x"), "octal": lambda v: _js_radix(v, "o"),
    "binary": lambda v: _js_radix(v, "b"), "bytes": lambda v: format_bytes(v, 1000),
    "sbytes": lambda v: format_smart_bytes(v, 1000), "sbytes10": lambda v: format_smart_bytes(v, 1000),
    "sbytes2": lambda v: format_smart_bytes(v, 1024), "rbytes": lambda v: format_bytes(v, 1000, True),
    "bytes10": lambda v: format_bytes(v, 1000), "rbytes10": lambda v: format_bytes(v, 1000, True),
    "bytes2": lambda v: format_bytes(v, 1024), "rbytes2": lambda v: format_bytes(v, 1024, True),
    "bitrate": lambda v: _bitrate(v), "rbitrate": lambda v: _bitrate(v, rnd=True),
    "sbitrate": lambda v: _bitrate(v, smart=True), "string": _js_str,
    "time": lambda v: format_duration(_norm_duration(v)), "star": lambda v: _stars(v, False),
    "pstar": lambda v: _stars(v, True),
}
_PREFIX_CMP = {
    "$": lambda v, c: v.startswith(c) if isinstance(v, str) else (v[0] == c if v else False),
    "^": lambda v, c: v.endswith(c) if isinstance(v, str) else (v[-1] == c if v else False),
    "~": lambda v, c: c in v,
    "=": lambda v, c: v == c,
    ">=": lambda v, c: v >= c, ">": lambda v, c: v > c,
    "<=": lambda v, c: v <= c, "<": lambda v, c: v < c,
}


def _quoted_args(inner):
    return [m[1] if m[1] is not None else m[2] for m in re.finditer(r'"([^"]*)"|\'([^\']*)\'', inner)]


def _ref_args(inner):
    stripped = re.sub(r'"[^"]*"|\'[^\']*\'', "", inner)
    return list(dict.fromkeys(re.findall(r"\{\s*([A-Za-z_]\w*\.[A-Za-z_]\w*)\s*\}", stripped)))


def _unquote(arg):
    return arg[1:-1] if len(arg) >= 2 and arg[0] in "'\"" and arg[-1] == arg[0] else None


def _read_field(path, pv):
    parts = path.strip().split(".")
    canon = _CANON.get(".".join(parts).lower()) if len(parts) == 2 else None
    return (pv.get(canon[0]) or {}).get(canon[1]) if canon else None


def _resolve_values(path, pv):
    v = _read_field(path, pv)
    if v is None:
        return None
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else [_js_str(v)]


def _list_arg(literals, refs, pv, lower=True):
    out = list(literals)
    for r in refs:
        out += [x.lower() if lower else x for x in (_resolve_values(r, pv) or [])]
    return out


def _replace_in(v, search, rep):
    if isinstance(v, str):
        return v.replace(search, rep)[:MAX_LEN]
    t = search.lower()
    return [rep if _js_str(x).lower() == t else x for x in v]


def _replaceable(v):
    return isinstance(v, str) or (isinstance(v, list) and not _is_obj_list(v))


def _compile_object_filter(conditions):
    tests = []
    for cond in conditions:
        m = re.match(r"^(!?)\s*([A-Za-z]+)\s*(?:([=~])(.*))?$", cond.strip(), re.S)
        if not m:
            raise ValueError(cond)
        neg, key, op, raw = m[1] == "!", m[2], m[3], m[4]
        if not op:
            tests.append(lambda o, pv, k=key, n=neg: _truthy(o.get(k)) != n)
            continue
        parts = [p.strip() for p in raw.split("|")]
        refs = [rm[1] for p in parts if (rm := _LOOSE_REF_PATH.match(p))]
        lits = [p.lower() for p in parts if not _LOOSE_REF_PATH.match(p) and p]

        def test(o, pv, k=key, n=neg, op=op, refs=refs, lits=lits):
            vals = _list_arg(lits, refs, pv) if refs else lits
            actual = o.get(k)
            entries = actual if isinstance(actual, list) else [actual]
            hit = any(e is not None and (_js_str(e).lower() in vals if op == "=" else
                                         any(x in _js_str(e).lower() for x in vals)) for e in entries)
            return hit != n

        tests.append(test)
    return lambda o, pv: all(t(o, pv) for t in tests)


_LOOSE_REF_PATH = re.compile(r"^\{\s*([A-Za-z_]\w*\.[A-Za-z_]\w*)\s*\}$")


def _compile_conditional(lower):
    exact = lower in EXACT_CONDS
    op = next((o for o in PREFIX_OPS if lower.startswith(o)), None)
    if not exact and not op:
        return None
    raw_check = lower[len(op):] if op else ""

    def apply(v, pv):
        try:
            if not is_present(v):
                return False
            if exact:
                return v is True if lower == "istrue" else v is False if lower == "isfalse" else True
            if _is_obj_list(v):
                return _ABSENT
            arr = [x.lower() for x in v] if isinstance(v, list) and all(isinstance(x, str) for x in v) else None
            sv = _js_str(v).lower()
            check = raw_check if re.search(r"\s", sv) else re.sub(r"\s", "", raw_check)
            nv, nc = _js_number(re.sub(r",\s", "", sv)), _js_number(re.sub(r",\s", "", check))
            numeric = op in ("<", "<=", ">", ">=", "=") and not math.isnan(nv) and not math.isnan(nc)
            if numeric:
                return _PREFIX_CMP[op](nv, nc)
            return _PREFIX_CMP[op](arr if op in "$^~" and arr is not None else sv, check)
        except Exception:
            return False

    return apply


def _compile_parameterised(source, lower):
    o = source.find("(")
    if o == -1 or not source.endswith(")"):
        return None
    name, inner = lower[:o], source[o + 1:-1]

    if name == "replace":
        vm = re.match(r"^\s*\{([^}]+)\}\s*,\s*(['\"])([\s\S]*)\2\s*$", inner)
        if vm:
            path, rep = vm[1], _subst_tools(vm[3])

            def apply(v, pv):
                if not _replaceable(v):
                    return _ABSENT
                r = _read_field(path, pv)
                return _replace_in(v, _js_str(r), rep) if r is not None and _js_str(r) else v
            return apply
        oq, cq = source[len("replace(")], source[-2]
        body = source[len("replace(") + 1:-2]
        pieces = re.split(re.escape(oq) + r"\s*,\s*" + re.escape(cq), body)
        if len(pieces) != 2 or not pieces[0]:
            return lambda v, pv: v if _replaceable(v) else _ABSENT
        search, rep = pieces[0], _subst_tools(pieces[1])
        key = search[1:-1] if search.startswith("{") and search.endswith("}") else None

        def apply(v, pv):
            if not _replaceable(v):
                return _ABSENT
            if not key:
                return _replace_in(v, search, rep)
            r = _read_field(key, pv)
            return _replace_in(v, _js_str(r), rep) if r is not None and _js_str(r) else v
        return apply

    if name in ("remove", "keep"):
        args, refs = _quoted_args(inner), _ref_args(inner)
        if not args and not refs:
            return lambda v, pv: _ABSENT
        targets = [a for a in args if a]
        lowered = list(dict.fromkeys(t.lower() for t in targets))
        keeping = name == "keep"

        def apply(v, pv):
            if isinstance(v, str):
                if keeping:
                    return _ABSENT
                for t in _list_arg(targets, refs, pv, lower=False):
                    if t:
                        v = v.replace(t, "")
                return v
            if isinstance(v, list) and not _is_obj_list(v):
                s = set(_list_arg(lowered, refs, pv))
                return [x for x in v if (_js_str(x).lower() in s) == keeping]
            return _ABSENT
        return apply

    if name == "join":
        raw = _unquote(inner)
        if raw is None:
            return None
        sep = _subst_tools(raw)
        return lambda v, pv: sep.join(_js_str(x) for x in v) if isinstance(v, list) and not _is_obj_list(v) else _ABSENT

    if name == "where":
        try:
            flt = _compile_object_filter(_quoted_args(inner))
        except ValueError:
            return lambda v, pv: _ABSENT
        return lambda v, pv: [x for x in v if flt(x, pv)] if isinstance(v, list) and (not v or _is_obj_list(v)) else _ABSENT

    if name == "pluck":
        key = _unquote(inner)
        if key is None:
            return None

        def apply(v, pv):
            if not isinstance(v, list) or (v and not _is_obj_list(v)):
                return _ABSENT
            found = []
            for item in v:
                f = item.get(key)
                for e in f if isinstance(f, list) else [f]:
                    if isinstance(e, str) and e and _sanitise(e) not in found:
                        found.append(_sanitise(e))
            return found
        return apply

    if name == "truncate":
        if not inner.isdigit():
            return None
        limit = int(inner)
        return lambda v, pv: (v if len(v) <= limit else v[:limit].rstrip() + "…") if isinstance(v, str) else _ABSENT

    if name == "slice":
        nums = [p.strip() for p in inner.split(",")]
        if not re.fullmatch(r"-?\d+", nums[0]):
            return None
        a = int(nums[0])
        b = int(nums[1]) if len(nums) > 1 and re.fullmatch(r"-?\d+", nums[1]) else None
        return lambda v, pv: v[a:b] if isinstance(v, list) else _ABSENT

    if name == "default":
        fb = _unquote(inner)
        if fb is None:
            return None
        return lambda v, pv: v if is_present(v) else fb

    if name == "trim":
        chars = _unquote(inner)
        if chars is None:
            return None
        return lambda v, pv: v.strip(chars) if isinstance(v, str) else _ABSENT

    if name == "translate":
        args = _quoted_args(inner)
        if len(args) < 2:
            return None
        return lambda v, pv: _map_chars(v, args[0], args[1]) if isinstance(v, str) else _ABSENT

    if name == "in":
        opts, refs = [a.lower() for a in _quoted_args(inner)], _ref_args(inner)
        if not opts and not refs:
            return None

        def apply(v, pv):
            if v is None:
                return False
            if _is_obj_list(v):
                return _ABSENT
            s = set(_list_arg(opts, refs, pv)) if refs else set(opts)
            if isinstance(v, list):
                return any(isinstance(x, str) and x.lower() in s for x in v)
            return _js_str(v).lower() in s
        return apply

    if name == "time":
        pat = _unquote(inner)
        if pat is None:
            return None
        return lambda v, pv: format_duration_pattern(_norm_duration(v), pat) if _is_num(v) else _ABSENT

    if name == "date":
        pat = _unquote(inner)
        if pat is None:
            return None
        return lambda v, pv: format_date_pattern(v, pat) if isinstance(v, str) else _ABSENT

    return None


def _compile_plain(lower):
    def apply(v, pv):
        if isinstance(v, str):
            fn = _STRING_FN.get(lower)
        elif isinstance(v, list):
            if _is_obj_list(v) and lower not in ("length", "reverse"):
                return _ABSENT
            fn = _ARRAY_FN.get(lower)
        elif isinstance(v, bool):
            fn = _js_str if lower == "string" else None
        elif _is_num(v):
            fn = _NUMBER_FN.get(lower)
        else:
            fn = None
        return fn(v) if fn else _ABSENT
    return apply


def compile_modifier(source):
    lower = source.lower()
    return _compile_conditional(lower) or _compile_parameterised(source, lower) or _compile_plain(lower)


#----- Renderer
_COMPARE = {
    "and": lambda a, b: b if _truthy(a) else a,
    "or": lambda a, b: a if _truthy(a) else b,
    "xor": lambda a, b: (_truthy(a) or _truthy(b)) and not (_truthy(a) and _truthy(b)),
    "neq": lambda a, b: a != b, "equal": lambda a, b: a == b,
    "left": lambda a, b: a, "right": lambda a, b: b,
}


class _Ctx:
    def __init__(self):
        self.budget, self.each_budget = MAX_LEN, 256


def _compile_each(source, depth, in_each):
    tpl = each_template(source)
    if tpl is None:
        return None
    if in_each:
        return lambda v, pv: _ABSENT
    render = compile_template(tpl, depth + 1, True)

    def apply(v, pv):
        if not isinstance(v, list) or (v and not _is_obj_list(v)):
            return _ABSENT
        out, ctx = [], pv["__ctx"]
        for track in v:
            if ctx.each_budget <= 0:
                break
            ctx.each_budget -= 1
            text = render({**pv, "track": track})
            if text.strip():
                out.append(text)
        return out
    return apply


def _prepare(operand, depth, in_each):
    return [(m, _compile_each(m, depth, in_each) or compile_modifier(m)) for m in operand["modifiers"]]


def _has_default(mods):
    return any(src.lower().startswith("default(") for src, _ in mods)


def _resolve_operand(operand, mods, pv):
    if operand["literal"] is not None:
        v = operand["literal"]
        for _, fn in mods:
            nxt = fn(v, pv)
            if nxt is _ABSENT:
                break
            v = nxt
        return v, None, True
    section = pv.get(operand["section"])
    if not isinstance(section, dict):
        return None, f"{{unknown_variableType({operand['section']})}}", False
    if operand["property"] not in section:
        return None, f"{{unknown_propertyName({operand['section']}.{operand['property']})}}", False
    prop = section[operand["property"]]
    v = _sanitise(prop) if isinstance(prop, str) else [_sanitise(x) if isinstance(x, str) else x for x in prop] \
        if isinstance(prop, list) else prop
    present = is_present(prop) or _has_default(mods)
    for src, fn in mods:
        inp = v
        v = fn(inp, pv)
        if v is _ABSENT:
            if inp is None:
                return "", None, present
            kind = "array" if isinstance(inp, list) else "boolean" if isinstance(inp, bool) else \
                "number" if _is_num(inp) else "string" if isinstance(inp, str) else "object"
            return None, f"{{unknown_{kind}_modifier({src})}}", present
        if isinstance(v, str) and len(v) > MAX_LEN:
            v = v[:MAX_LEN]
            break
    if _is_obj_list(v):
        return None, f"{{unrenderable_list({operand['section']}.{operand['property']})}}", present
    return v, None, present


def _operand_presence(operand, mods, pv):
    if operand["literal"] is not None or _has_default(mods):
        return True
    section = pv.get(operand["section"])
    return isinstance(section, dict) and is_present(section.get(operand["property"]))


def _resolve_expression(node, prepared, pv):
    ops = node["operands"]
    if len(ops) == 1:
        return _resolve_operand(ops[0], prepared[0], pv)
    comps = node["comparators"]
    present = _operand_presence(ops[0], prepared[0], pv)
    for i in range(1, len(ops)):
        nxt = _operand_presence(ops[i], prepared[i], pv)
        present = (present or nxt) if comps[i - 1] == "or" else (present and nxt)
    short = len(set(comps)) == 1 and comps[0] in ("and", "or")
    result, err, _ = _resolve_operand(ops[0], prepared[0], pv)
    for i in range(1, len(ops)):
        if err:
            return None, err, present
        comp = comps[i - 1]
        if short and comp == "and" and result is False:
            return False, None, present
        if short and comp == "or" and result is True:
            return True, None, present
        nxt, err, _ = _resolve_operand(ops[i], prepared[i], pv)
        if err:
            return None, err, present
        result = _COMPARE[comp](result, nxt)
    return result, None, present


def _emit(render, pv):
    ctx = pv["__ctx"]
    if ctx.budget <= 0:
        return ""
    text = render(pv)
    ctx.budget -= len(text)
    return text


def _compile_node(node, depth, in_each):
    kind = node["kind"]
    if kind == "raw":
        text = node["text"].replace("\\n", "\n")
        return lambda pv: text
    if kind == "tool":
        s = NL if node["tool"] == "newLine" else RL
        return lambda pv: s
    if kind == "group":
        parts = []
        for child in node["nodes"]:
            prep = [_prepare(o, depth, in_each) for o in child["operands"]] \
                if child["kind"] == "expr" and not child["check"] else None
            parts.append((child, _compile_node(child, depth, in_each), prep))

        def group(pv):
            out = ""
            for child, render, prep in parts:
                if prep is not None:
                    _, err, present = _resolve_expression(child, prep, pv)
                    if not err and not present:
                        return ""
                out += _emit(render, pv)
            return out
        return group

    prepared = [_prepare(o, depth, in_each) for o in node["operands"]]
    if not node["check"]:
        def expr(pv):
            v, err, _ = _resolve_expression(node, prepared, pv)
            return err or _js_str(v)
        return expr

    t, f, a = (compile_template(b, depth + 1, in_each) if b is not None else None for b in node["check"])

    def check(pv):
        v, err, _ = _resolve_expression(node, prepared, pv)
        if err:
            return err
        if not is_present(v):
            return a(pv) if a else ""
        if v is not True and v is not False:
            return f"{{cannot_coerce_boolean_for_check_from({_js_str(v)})}}"
        return t(pv) if v else f(pv)
    return check


def compile_template(template, depth=0, in_each=False):
    if depth > MAX_DEPTH:
        return lambda pv: template
    parts = [_compile_node(n, depth, in_each) for n in parse_template(template)]

    def render(pv):
        return "".join(_emit(p, pv) for p in parts)

    if depth > 0:
        return render

    def top(pv):
        pv = {**pv, "__ctx": _Ctx()}
        return render(pv)
    return top


_SEP_RUN = re.compile(r"\s+([|·•/])(?:\s+[|·•/])+(?=\s)")
_SEP_EDGE = re.compile(r"^\s*[|·•/]\s+|\s+[|·•/]\s*$")


#----- AIOStreams line rules, plus collapsing separators left around empty values
def _finalise(text):
    lines = []
    for line in text.split("\n"):
        if not line.strip() or RL in line:
            continue
        line = _SEP_EDGE.sub("", _SEP_RUN.sub(r" \1", line))
        if line.strip():
            lines.append(line)
    return "\n".join(lines).replace(NL, "\n")


_CACHE: dict = {}


def render(template, pv):
    fn = _CACHE.get(template)
    if fn is None:
        if len(_CACHE) > 64:
            _CACHE.clear()
        fn = _CACHE[template] = compile_template(template)
    return _finalise(fn(pv))


#----- Filename parsing (AIOStreams patterns, adapted to Python's re)
def _rx(pattern):
    return re.compile(r"(?<![^\s\[(_\-.,])(" + pattern + r")(?=[\s\[()\]_.\-,]|$)", re.I)


_RESOLUTIONS = [
    ("2160p", _rx(r"(bd|hd|m)?(4k|2160(p|i)?)|u(ltra)?[ .\-_]?hd|3840\s?x\s?(\d{4})")),
    ("1440p", _rx(r"(bd|hd|m)?(1440(p|i)?)|2k|w?q(uad)?[ .\-_]?hd|2560\s?x(\d{4})")),
    ("1080p", _rx(r"(bd|hd|m)?(1080(p|i)?)|f(ull)?[ .\-_]?hd|1920\s?x\s?(\d{3,4})")),
    ("720p", _rx(r"(bd|hd|m)?((720|800)(p|i)?)|hd|1280\s?x\s?(\d{3,4})")),
    ("576p", _rx(r"(bd|hd|m)?((576|534)(p|i)?)")),
    ("480p", _rx(r"(bd|hd|m)?(480(p|i)?)|sd")),
    ("360p", _rx(r"(bd|hd|m)?(360(p|i)?)")),
    ("240p", _rx(r"(bd|hd|m)?(240(p|i)?)")),
    ("144p", _rx(r"(bd|hd|m)?(144(p|i)?)")),
]
_QUALITIES = [
    ("BluRay REMUX", _rx(r"(bd|br|b|uhd)?remux"), lambda f: "dvd" not in f.lower()),
    ("BluRay", _rx(r"(bd|blu[ .\-_]?ray)([ .\-_]?rip)?|br[ .\-_]?rip"), lambda f: "remux" not in f.lower()),
    ("WEB-DL", _rx(r"web[ .\-_]?(dl)?(?![ .\-_]?(rip|DLRip|cam))"), None),
    ("WEBRip", _rx(r"web[ .\-_]?rip"), None),
    ("HDRip", _rx(r"hd[ .\-_]?rip|web[ .\-_]?dl[ .\-_]?rip"), None),
    ("HC HD-Rip", _rx(r"hc|hd[ .\-_]?rip"), None),
    ("DVD REMUX", _rx(r"(hd[ .\-_]?)?dvd.*remux|remux.*(hd[ .\-_]?)?dvd"), None),
    ("DVDRip", _rx(r"dvd[ .\-_]?(rip|mux|r|full|5|9)?"), None),
    ("HDTV", _rx(r"(hd|pd)tv|tv[ .\-_]?rip|hdtv[ .\-_]?rip|dsr(ip)?|sat[ .\-_]?rip"), None),
    ("CAM", _rx(r"cam|hdcam|cam[ .\-_]?rip"), None),
    ("TS", _rx(r"telesync|ts|hd[ .\-_]?ts|pdvd|predvd(rip)?"), None),
    ("TC", _rx(r"telecine|tc|hd[ .\-_]?tc"), None),
    ("SCR", _rx(r"((dvd|bd|web|hd)?[ .\-_]?)?(scr(eener)?)"), None),
]
_VISUAL = [
    ("10bit", _rx(r"10[ .\-_]?bit|hi10p?")),
    ("HDR10+", _rx(r"hdr[ .\-_]?10[ .\-_]?(p(lus)?|[+])")),
    ("HDR10", _rx(r"hdr[ .\-_]?10(?![ .\-_]?(?:\+|p(?:lus)?|bit|hi))")),
    ("HDR", _rx(r"hdr(?![ .\-_]?(?:10(?![ .\-_]?(?:bit|hi))|\+|p(?:lus)?))")),
    ("HLG", _rx(r"hlg")),
    ("DV", _rx(r"do?(lby)?[ .\-_]?vi?(sion)?(?:[ .\-_]?atmos)?|dv")),
    ("3D", _rx(r"(bd)?(3|three)[ .\-_]?(d(imension)?(al)?)")),
    ("IMAX", _rx(r"imax")),
    ("AI", _rx(r"ai|ai(enhanced?|re[ .\-_]?graded?)")),
    ("Upscaled", _rx(r"(ai)?(uprez|ups(uhd|cal(ed?|ing)(uhd)?))(ai)?")),
    ("SDR", _rx(r"sdr")),
    ("H-OU", _rx(r"h?(alf)?[ .\-_]?(ou|over[ .\-_]?under)")),
    ("H-SBS", _rx(r"h?(alf)?[ .\-_]?(sbs|side[ .\-_]?by[ .\-_]?side)")),
]
_AUDIO = [
    ("Atmos", _rx(r"atmos|ddpa\d?")),
    ("DD+", _rx(r"(d(olby)?[ .\-_]?d(igital)?[ .\-_]?((p(lus)?|\+)a?)(?:[ .\-_]?(2[ .\-_]?0|5[ .\-_]?1|7[ .\-_]?1))?)|e[ .\-_]?ac[ .\-_]?3")),
    ("DD", _rx(r"(d(olby)?[ .\-_]?d(igital)?(?:[ .\-_]?(5[ .\-_]?1|7[ .\-_]?1|2[ .\-_]?0?))?)|(?<!e)(?<!e[ .\-_])ac[ .\-_]?3")),
    ("DTS:X", _rx(r"dts[ .\-:_]?x")),
    ("DTS-HD MA", _rx(r"dts[ .\-_]?hd[ .\-_]?ma")),
    ("DTS-HD", _rx(r"dts[ .\-_]?hd(?![ .\-_]?ma)")),
    ("DTS-ES", _rx(r"dts[ .\-_]?es")),
    ("DTS", _rx(r"dts(?![ .\-:_]?(x(?=[\s\[()\]_.\-,]|$)|hd[ .\-_]?(ma)?|es))")),
    ("TrueHD", _rx(r"true[ .\-_]?hd")),
    ("PCM", _rx(r"l?pcm")),
    ("OPUS", _rx(r"opus")),
    ("AAC", _rx(r"q?aac(?:[ .\-_]?2)?")),
    ("FLAC", _rx(r"flac(?:[ .\-_]?(lossless|2\.0|x[2-4]))?")),
]
_CHANNELS = [
    ("2.0", _rx(r"(d(olby)?[ .\-_]?d(igital)?)?2[ .\-_]?0(ch)?")),
    ("5.1", _rx(r"(d(olby)?[ .\-_]?d(igital)?[ .\-_]?((p(lus)?|\+)a?)?)?5[ .\-_]?1(ch)?")),
    ("6.1", _rx(r"(d(olby)?[ .\-_]?d(igital)?[ .\-_]?((p(lus)?|\+)a?)?)?6[ .\-_]?1(ch)?")),
    ("7.1", _rx(r"(d(olby)?[ .\-_]?d(igital)?[ .\-_]?((p(lus)?|\+)a?)?)?7[ .\-_]?1(ch)?")),
]
_ENCODES = [
    ("HEVC", _rx(r"hevc[ .\-_]?(10)?|[xh][ .\-_]?265")),
    ("AVC", _rx(r"avc|[xh][ .\-_]?264")),
    ("AV1", _rx(r"av1")),
    ("VC-1", _rx(r"vc[ .\-_]?1")),
    ("XviD", _rx(r"xvid")),
    ("DivX", _rx(r"divx|dvix")),
    ("MPEG-4", _rx(r"mpeg[ .\-_]?4")),
]
_LANG_RX = [(n, _rx(p + r"(?![ .\-_]?sub(title)?s?)")) for n, (_, _, p) in LANGUAGES.items()]
_EDITIONS = [
    ("Extended Edition", _rx(r"extended([ .\-_]?(cut|edition))?")),
    ("Director's Cut", _rx(r"director'?s?[ .\-_]?cut|dc")),
    ("Theatrical", _rx(r"theatrical([ .\-_]?(cut|edition))?")),
    ("Ultimate Edition", _rx(r"ultimate[ .\-_]?(cut|edition)")),
    ("Collector's Edition", _rx(r"collector'?s?[ .\-_]?edition")),
    ("Criterion Collection", _rx(r"criterion")),
    ("Anniversary Edition", _rx(r"(\d+(th)?[ .\-_]?)?anniversary([ .\-_]?edition)?")),
    ("IMAX", _rx(r"imax([ .\-_]?edition)?")),
    ("Remastered", _rx(r"remaster(ed)?")),
    ("Uncut", _rx(r"uncut")),
]
_NETWORKS = [
    ("Netflix", _rx(r"nf|netflix")), ("Amazon", _rx(r"amzn|amazon")), ("Disney+", _rx(r"dsnp|disney\+?")),
    ("Apple TV+", _rx(r"atvp|aptv")), ("HBO Max", _rx(r"hmax|max")), ("Hulu", _rx(r"hulu")),
    ("Peacock", _rx(r"pcok|peacock")), ("Paramount+", _rx(r"pmtp|paramount\+?")),
    ("JioCinema", _rx(r"jc|jiocinema")), ("JioHotstar", _rx(r"jhs|hs|hotstar")), ("Zee5", _rx(r"zee5")),
    ("SonyLIV", _rx(r"sonyliv|sliv")), ("Crunchyroll", _rx(r"cr|crunchyroll")),
]


def _first(name, table):
    return next((tag for tag, rx, *extra in table if rx.search(name) and (not extra or not extra[0] or extra[0](name))), None)


def _all(name, table):
    return [tag for tag, rx, *_ in table if rx.search(name)]


def _flag(rx, name):
    return bool(re.search(r"(?<![a-z])(?:" + rx + r")(?![a-z])", name, re.I))


def _as_list(v):
    if v is None:
        return []
    return [int(x) for x in (v if isinstance(v, list) else [v]) if str(x).isdigit()]


def parse_filename(filename):
    try:
        p = PTN.parse(filename) or {}
    except Exception:
        p = {}
    stem = re.sub(r"\.(mkv|mp4|avi|webm|m4v|ts|mov|wmv|flv|zip|\d{3})$", "", filename, flags=re.I)
    title = p.get("title")
    rest = stem
    if title and len(title) > 4:
        rest = re.sub(re.escape(title).replace(r"\ ", "[._ ]"), "", stem, count=1, flags=re.I).strip()
        rest = re.sub(r"\s+", ".", rest).strip(".")
    res = _first(rest, _RESOLUTIONS)
    langs = _all(re.sub(r"ind", "", rest, flags=re.I) if str(p.get("encoder", "")).lower() == "ind" else rest, _LANG_RX)
    for lang in p.get("language") or []:
        name = normalise_language(lang) or (lang if lang in LANGUAGES else None)
        if name and name not in langs:
            langs.append(name)
    seasons, episodes = _as_list(p.get("season")), _as_list(p.get("episode"))
    ext = re.search(r"\.([A-Za-z0-9]{2,4})$", filename)
    return {
        "title": title, "year": str(p["year"]) if p.get("year") else None,
        "resolution": res, "quality": _first(rest, _QUALITIES), "encode": _first(rest, _ENCODES),
        "visualTags": _all(rest, _VISUAL), "audioTags": _all(rest, _AUDIO), "audioChannels": _all(rest, _CHANNELS),
        "languages": langs, "releaseGroup": p.get("group") or p.get("encoder") or p.get("site"), "site": p.get("site"),
        "editions": _all(rest, _EDITIONS), "network": _first(rest, _NETWORKS),
        "repack": _flag(r"repack|rerip", rest), "proper": _flag(r"proper", rest),
        "uncensored": _flag(r"uncensored", rest), "unrated": _flag(r"unrated", rest),
        "remastered": _flag(r"remaster(ed)?", rest), "regraded": _flag(r"regrad(e|ed)", rest),
        "upscaled": _flag(r"upscal(e|ed|ing)|uprez", rest),
        "subbed": _flag(r"subs?|subbed|esubs?|msubs?", rest), "dubbed": _flag(r"dub(s|bed)?", rest),
        "seasons": seasons, "episodes": episodes,
        "extension": ext[1].lower() if ext else None,
        "container": (p.get("container") or (ext[1] if ext else None) or None),
        "episodeTitle": p.get("episodeName"),
    }


#----- Build the AIOStreams-shaped value tree for one Telegram stream
def build_parse_value(filename, size_bytes, *, addon_name="Telegram", is_split=False, parts=0,
                      channel=None, proxied=False, meta=None):
    f = parse_filename(filename)
    meta = meta or {}
    stream = {k: None for k in FIELDS["stream"]}
    seasons, episodes = f["seasons"], f["episodes"]
    fs = None if not seasons else f"S{seasons[0]:02d}" if len(seasons) == 1 else f"S{seasons[0]:02d}-{seasons[-1]:02d}"
    fe = None if not episodes else f"E{episodes[0]:02d}" if len(episodes) == 1 else f"E{episodes[0]:02d}-{episodes[-1]:02d}"
    langs = f["languages"]
    codes = _map_langs(langs, language_code)
    stream.update({
        "filename": filename, "size": size_bytes or 0, "library": False, "private": False, "freeleech": False,
        "quality": f["quality"], "resolution": f["resolution"], "encode": f["encode"],
        "visualTags": f["visualTags"], "audioTags": f["audioTags"], "audioChannels": f["audioChannels"],
        "languages": langs, "uLanguages": [], "subtitles": [], "uSubtitles": [],
        "languageEmojis": _map_langs(langs, language_emoji), "uLanguageEmojis": [],
        "subtitleEmojis": [], "uSubtitleEmojis": [],
        "languageCodes": codes, "uLanguageCodes": [], "subtitleCodes": [], "uSubtitleCodes": [],
        "smallLanguageCodes": _map_langs(langs, lambda x: make_small((LANGUAGES.get(x) or ("",))[0] or x)),
        "uSmallLanguageCodes": [], "smallSubtitleCodes": [], "uSmallSubtitleCodes": [],
        "wedontknowwhatakilometeris": _map_langs(langs, lambda x: language_emoji(x).replace("🇬🇧", "🇺🇸🦅")),
        "uWedontknowwhatakilometeris": [], "audioTracks": [], "subtitleTracks": [],
        "releaseGroup": f["releaseGroup"], "editions": f["editions"],
        "edition": f["editions"][0] if f["editions"] else None,
        "remastered": f["remastered"], "regraded": f["regraded"], "repack": f["repack"], "proper": f["proper"],
        "uncensored": f["uncensored"], "unrated": f["unrated"], "upscaled": f["upscaled"],
        "subbed": f["subbed"], "dubbed": f["dubbed"], "network": f["network"],
        "site": f["site"], "container": f["container"], "extension": f["extension"], "indexer": channel, "channel": channel,
        "year": f["year"], "title": f["title"], "episodeTitle": f["episodeTitle"],
        "seasons": seasons, "season": seasons[0] if seasons else None, "formattedSeasons": fs,
        "episodes": episodes, "episode": episodes[0] if episodes else None, "formattedEpisodes": fe,
        "seasonEpisode": [x for x in (fs, fe) if x], "seasonPack": bool(seasons and not episodes),
        "type": "http", "proxied": proxied, "parts": parts or None,
        "message": f"Split into {parts} parts" if is_split and parts else ("Split file" if is_split else None),
        "seadex": False, "seadexBest": False, "regexMatched": None, "rseMatched": [], "seMatched": [],
        "preloading": False,
    })
    metadata = {k: None for k in FIELDS["metadata"]}
    metadata.update({k: v for k, v in meta.items() if k in metadata})
    pv = {
        "config": {"addonName": addon_name},
        "stream": stream,
        "metadata": metadata,
        "user": {k: [] for k in FIELDS["user"]},
        "service": {"id": None, "shortName": None, "name": None, "cached": None},
        "addon": {"name": addon_name, "presetId": "telegram", "manifestUrl": None},
    }
    dump = {k: v for k, v in pv.items()}
    pv["debug"] = {"json": json.dumps(dump, ensure_ascii=False, default=str),
                   "jsonf": json.dumps(dump, ensure_ascii=False, default=str, indent=2)}
    return pv


#----- Render name/description for the active formatter; None means use the built-in format
def format_stream(settings, filename, size_bytes, **kw):
    preset = settings.stream_formatter
    if preset == "default":
        return None
    tpl = PRESETS.get(preset) or {"name": settings.formatter_name, "description": settings.formatter_description}
    if not (tpl["name"] or tpl["description"]):
        return None
    try:
        pv = build_parse_value(filename, size_bytes, **kw)
        return render(tpl["name"], pv), render(tpl["description"], pv)
    except Exception as e:
        LOGGER.warning(f"[FORMATTER] {preset} failed for {filename}: {e}")
        return None


if __name__ == "__main__":
    pv = build_parse_value(
        "Dune.Part.Two.2024.2160p.UHD.BluRay.REMUX.DV.HDR10.HEVC.TrueHD.Atmos.7.1.ITA.ENG-GROUP.mkv",
        65_000_000_000, addon_name="Telegram", channel="Movies HQ", meta={"queryType": "movie", "type": "movie"},
    )
    s = pv["stream"]
    assert s["resolution"] == "2160p" and s["quality"] == "BluRay REMUX" and s["encode"] == "HEVC", s
    assert {"DV", "HDR10"} <= set(s["visualTags"]) and {"TrueHD", "Atmos"} <= set(s["audioTags"]), s
    assert {"Italian", "English"} <= set(s["languages"]), s["languages"]
    assert render('{stream.size::sbytes}', pv) == "65 GB"
    assert render('{?🌐 {stream.subtitles::join(", ")}?}x', pv) == "x"
    assert render('{stream.subtitles::exists["Y"||"N"]}|{stream.resolution::=2160p["4K"||"no"]}', pv) == "N|4K"
    assert render("{stream.languages::join(' / ')::upper}", pv) == "ENGLISH / ITALIAN"
    assert render("{stream.visualTags::in('DV','HDR')[\"✦\"||\"✧\"]}", pv) == "✦"
    assert render("{stream.missing}", pv) == "{invalid_expression(stream.missing)}"
    assert render("{'n/a'::upper}", pv) == "N/A"
    assert render("a\n\n{tools.removeLine}b\nc", pv) == "a\nc"
    assert render("🎧 TrueHD |  | 7.1 · ", pv) == "🎧 TrueHD | 7.1"
    assert render("https://x.y/a", pv) == "https://x.y/a"
    assert render("{stream.seasonEpisode::join('·')::translate('0123456789','₀₁₂₃₄₅₆₇₈₉')}", pv) == ""
    for key, tpl in PRESETS.items():
        n, d = render(tpl["name"], pv), render(tpl["description"], pv)
        assert "unknown_" not in n + d and "invalid_expression" not in n + d and "cannot_coerce" not in n + d, (key, n, d)
        print(f"--- {key}\n{n}\n{d}")
    print("ok")
