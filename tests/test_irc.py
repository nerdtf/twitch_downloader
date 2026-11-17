from clipper.chat.irc import parse_line, parse_tags, to_chat_row

PRIVMSG = (
    "@badge-info=subscriber/14;badges=subscriber/12,premium/1;color=#FF0000;"
    "display-name=Вася;emotes=25:6-10;first-msg=0;id=abc;mod=0;room-id=111;"
    "tmi-sent-ts=1700000000123;user-id=222;user-type= "
    ":vasya!vasya@vasya.tmi.twitch.tv PRIVMSG #Streamer :ахаха Kappa ору :)"
)
SUB = (
    r"@badges=subscriber/0;display-name=Pete;login=pete;msg-id=resub;"
    r"msg-param-cumulative-months=6;system-msg=Pete\ssubscribed\sfor\s6\smonths!;"
    r"tmi-sent-ts=1700000001000;user-id=333 "
    ":tmi.twitch.tv USERNOTICE #streamer :Great stream!"
)
RAID = (
    r"@display-name=Raider;login=raider;msg-id=raid;msg-param-viewerCount=1500;"
    r"tmi-sent-ts=1700000002000;user-id=444 :tmi.twitch.tv USERNOTICE #streamer"
)
BITS = (
    "@bits=100;display-name=X;tmi-sent-ts=1700000003000;user-id=5 "
    ":x!x@x.tmi.twitch.tv PRIVMSG #streamer :cheer100 lol"
)
CLEARCHAT = (
    "@ban-duration=600;room-id=1;tmi-sent-ts=1700000004000 "
    ":tmi.twitch.tv CLEARCHAT #streamer :baduser"
)


def test_tag_unescape():
    assert parse_tags(r"a=hello\sworld;b=semi\:colon;c=back\\slash;d=") == {
        "a": "hello world", "b": "semi;colon", "c": "back\\slash", "d": "",
    }  # fmt: skip


def test_privmsg_row():
    row = to_chat_row(parse_line(PRIVMSG), recv_ms=1)
    assert row["channel"] == "streamer"
    assert row["kind"] == "msg"
    assert row["sent_ms"] == 1700000000123
    assert row["login"] == "vasya" and row["display_name"] == "Вася"
    assert row["user_id"] == "222"
    assert row["text"] == "ахаха Kappa ору :)"
    assert row["emotes"] == "25:6-10"
    assert row["badges"] == "subscriber/12,premium/1"
    assert row["bits"] is None


def test_usernotice_resub_and_raid():
    sub = to_chat_row(parse_line(SUB), recv_ms=1)
    assert (sub["kind"], sub["msg_id"], sub["login"], sub["text"]) == (
        "usernotice", "resub", "pete", "Great stream!",
    )  # fmt: skip
    raid = to_chat_row(parse_line(RAID), recv_ms=1)
    assert raid["msg_id"] == "raid" and raid["text"] is None
    assert '"msg-param-viewerCount": "1500"' in raid["tags"]


def test_bits_and_clearchat():
    assert to_chat_row(parse_line(BITS), 1)["bits"] == 100
    cc = to_chat_row(parse_line(CLEARCHAT), 1)
    assert cc["kind"] == "clearchat" and cc["login"] == "baduser" and cc["text"] is None


def test_non_chat_lines_ignored():
    assert to_chat_row(parse_line("PING :tmi.twitch.tv"), 1) is None
    assert to_chat_row(parse_line(":tmi.twitch.tv 001 justinfan1 :Welcome, GLHF!"), 1) is None
    assert parse_line("") is None


def test_missing_sent_ts_falls_back_to_recv():
    row = to_chat_row(parse_line(":a!a@a PRIVMSG #c :hi"), recv_ms=42)
    assert row["sent_ms"] == 42 and row["tags"] is None
