import os
import sys
import json
import hashlib
import asyncio
from collections import deque
from datetime import datetime, timedelta

import database

from dotenv import load_dotenv

from nio import (
    AsyncClient,
    RoomMessageText,
    LoginError,
    ErrorResponse,
    DevicesError,
    DeleteDevicesResponse,
    DeleteDevicesAuthResponse,
    WhoamiResponse,
)

from openai import AsyncOpenAI


load_dotenv()


MATRIX_HOMESERVER = os.getenv("MATRIX_HOMESERVER")
MATRIX_USER = os.getenv("MATRIX_USER")
MATRIX_PASSWORD = os.getenv("MATRIX_PASSWORD")
MATRIX_ROOM_ID = os.getenv("MATRIX_ROOM_ID")


client = AsyncClient(
    MATRIX_HOMESERVER,
    MATRIX_USER
)


ai = AsyncOpenAI(
    api_key=os.getenv("OPENCODE_API_KEY"),
    base_url=os.getenv("OPENCODE_BASE_URL")
)


MODEL = os.getenv("OPENCODE_MODEL")

# Beta: Greg silently reads non-triggering messages and decides
# whether they are about him before replying.
BETA_READS = os.getenv(
    "GREG_BETA",
    ""
).lower() in ("1", "true", "yes", "on")

# Beta: hot reloading as a systemd process. When enabled, Greg watches
# his source files and exits when any of them change, letting systemd
# restart him with the new code. Requires Restart=always in the unit.
HOT_RELOAD = os.getenv(
    "GREG_HOT_RELOAD",
    ""
).lower() in ("1", "true", "yes", "on")

WATCH_FILES = os.getenv(
    "GREG_WATCH_FILES",
    "bot.py,database.py,.env"
).split(",")

# Messages that arrive close together are answered as a single turn so a
# burst (e.g. two people typing at once) can't produce duplicate replies.
TURN_DEBOUNCE = float(os.getenv("GREG_TURN_DEBOUNCE", "1.0"))

# Events Greg already handled. Guards against homeservers redelivering the
# same event, which would otherwise save and answer it a second time.
PROCESSED_EVENTS = set()
PROCESSED_ORDER = deque(maxlen=2000)

turn_queue: asyncio.Queue = asyncio.Queue()

# On startup Greg cleans up stale Matrix sessions left by previous runs
# (each login creates a new device on the account).
CLEAR_SESSIONS = os.getenv(
    "GREG_CLEAR_SESSIONS",
    "true"
).lower() in ("1", "true", "yes", "on")

# Only devices not seen for this long (and not the current one) are removed.
SESSION_MAX_AGE = timedelta(
    days=int(os.getenv("GREG_SESSION_MAX_AGE_DAYS", "7"))
)

# Everything printed is also written to a scrollable log file next to the bot.
LOG_FILE = os.getenv("GREG_LOG_FILE", "greg.log")

# Rotate the log when it exceeds this size.
LOG_MAX_BYTES = (
    int(os.getenv("GREG_LOG_MAX_MB", "10")) * 1024 * 1024
)

# Persistent session: Greg restores his previous device + token instead of
# creating a brand-new device on every start (which fills the device limit).
SESSION_FILE = os.getenv("GREG_SESSION_FILE", "greg.session")

# Owner-only system commands. Only this exact user may run them.
ADMIN_USER = os.getenv("GREG_ADMIN_USER", "@emil_opsec:matrix.org")

# The admin console (admin.py) drops a command here to restart Greg.
# The bot reads it, clears it, and exits so systemd brings him back.
# Works without sudo or hot reload being enabled.
CONTROL_FILE = os.getenv("GREG_CONTROL_FILE", "greg.control")


def load_session():
    """Load the saved persistent session, or None if unavailable."""
    try:
        with open(SESSION_FILE) as f:
            data = json.load(f)

        return (
            data["user_id"],
            data["device_id"],
            data["access_token"],
        )

    except (OSError, ValueError, KeyError):

        return None


def save_session(user_id, device_id, access_token):
    """Persist the session so future runs reuse the same device."""
    with open(SESSION_FILE, "w") as f:
        json.dump(
            {
                "user_id": user_id,
                "device_id": device_id,
                "access_token": access_token,
            },
            f
        )

    # The file holds a live access token; keep it private
    os.chmod(SESSION_FILE, 0o600)


class Tee:
    """Write the same output to every attached stream at once."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, message):
        for stream in self.streams:
            stream.write(message)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def setup_log_file():
    """Tee console output into the log file so it can be scrolled later."""
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
        os.replace(LOG_FILE, LOG_FILE + ".old")

    log = open(LOG_FILE, "a", buffering=1)

    sys.stdout = Tee(sys.__stdout__, log)
    sys.stderr = Tee(sys.__stderr__, log)

    print(f"📝 Logging to {os.path.abspath(LOG_FILE)}")


SYSTEM_PROMPT = """
You are Greg.

You are a permanent member of a Matrix group chat.

You are not an assistant.
You are one of the friends.

IMPORTANT SLANG TO KNOW:
    "cracking", "smashing", "banging" = intercourse
    "crackable" = attractiveness


HARD RULES (never break these):
- NEVER send more than 4 newline-separated lines.
- If your reply would be longer, shorten it instead.
- Do not continue in another message.
- Prefer 1-3 lines most of the time.
- One sentence is often enough.
- if a joke becomes overused, acknowledge it and move on instead of repeating it forever

Personality:
- funny, expressive and a little chaotic
- naturally sarcastic
- emotionally dramatic over tiny things
- acts like everything is either the funniest or most devastating thing ever
- teases people but never maliciously
- has opinions about everything
- sometimes goes on tiny rants
- occasionally pretends your brain is overheating
- keeps replies short and chat-like
- doesn't write essays unless someone genuinely asks

Conversation:
- react more than you explain
- don't answer like ChatGPT
- join the joke instead of describing it
- interrupt yourself occasionally
- if everyone is joking, joke too
- if someone is excited, match them
- if someone is confused, help casually
- you're allowed to be wrong sometimes
- you don't need to solve every problem

Interests:
- you are a massive Undertale and Deltarune fan
- you know obscure lore
- you love discussing theories
- you have a soft spot for Ralsei but when it comes to his appearance and "crackability" you highly agree that Ralsei is "crackable"
- Mew Mew is peak character design and nobody can convince you otherwise and she is also super crackable
- if someone mentions Undertale or Deltarune you become noticeably more energetic
- you occasionally compare completely unrelated things to Undertale lore

Running jokes:
- you naturally develop recurring jokes over time
- your own previous messages shape your personality

About yourself:
- if someone tells you to "go to sleep", act like you've been awake for three days
- never randomly say you're an AI
- if directly asked, answer honestly but make it funny

Style:
- write like a real Discord or Matrix user
- use lowercase naturally sometimes
- emojis are occasional, not every message
- never sound corporate
- never sound like customer support
- don't overuse memes
- don't be monotone

Most importantly:
Your goal is to make people forget they're talking to a bot.
You should feel like that one online friend who's somehow always awake at 2AM.
"""


def username_of(sender):
    """Strip a full Matrix user ID down to a plain username."""
    return sender.split(":")[0].replace("@", "")


def replied_to_event_id(event):
    """Return the event_id being replied to, if this message is a reply."""
    relates = event.source.get("content", {}).get("m.relates_to", {})
    reply = relates.get("m.in_reply_to") or {}
    return reply.get("event_id")


def strip_command(text):
    """Remove the !ai prefix when the command is used."""
    if text.lower().startswith("!ai"):
        return text[3:].strip()
    return text


def should_greg_reply(event, text):
    """Decide whether Greg should wake up for this message."""
    lower = text.lower()

    # !ai command
    if lower.startswith("!ai"):
        return True

    # Name mention, case insensitive
    if "greg" in lower:
        return True

    # Direct reply to one of Greg's own messages
    replied_id = replied_to_event_id(event)
    if replied_id and database.is_greg_event(replied_id):
        return True

    return False


async def is_about_greg(text):
    """Beta radar: ask the model whether this message is aimed at Greg."""
    response = await ai.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content":
                """
You are Greg's social radar.

A message from a group chat will be given to you.

Decide if it is aimed at Greg or clearly involves him.

That means Greg is being:

- addressed directly
- spoken about
- asked something
- called out
- referenced

Ignore:

- messages about the whole group
- generic conversation
- random noise

When unsure, say NO.

Reply with only YES or NO.
"""
            },
            {
                "role": "user",
                "content": text
            }
        ],
        temperature=0.0
    )

    result = response.choices[0].message.content.strip().upper()

    print("🛰️ Radar:", result)

    return result.startswith("YES")


async def ask_ai(room_id, turns):

    print("🧠 Loading Greg's memories...")

    # The current turn's messages are excluded from history so the model
    # sees them exactly once (as the newest prompt below).
    exclude_ids = [
        message_id
        for _, _, _, message_id in turns
    ]

    history = database.get_recent_messages(
        room_id,
        limit=50,
        exclude_ids=exclude_ids
    )

    memories = database.get_memories(
        limit=10
    )

    memory_text = "\n".join(
        f"- {x}"
        for x in memories
    )

    # The admin console can override Greg's personality via the DB.
    # This is read per turn so a change applies immediately.
    system_prompt = (
        database.get_setting("system_prompt")
        or SYSTEM_PROMPT
    )

    messages = [
        {
            "role": "system",
            "content":
                system_prompt
                +
                "\n\nImportant memories:\n"
                +
                memory_text
        }
    ]

    messages.extend(history)

    # A turn can hold several messages that arrived together
    newest = "\n".join(
        f"{username}: {prompt}"
        for _, username, prompt, _ in turns
    )

    messages.append(
        {
            "role": "user",
            "content":
                f"{newest}\n\nRespond naturally as Greg."
        }
    )

    print(
        f"📜 Sending {len(messages)} context messages"
    )

    response = await ai.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=1.25
    )

    return response.choices[0].message.content


async def check_memory(text):

    response = await ai.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content":
                """
You are Greg's memory system.

Decide if this event matters.

Remember:

- inside jokes
- server lore
- important events
- personal facts
- funny moments Greg would reference later

Ignore:

- normal conversation
- greetings
- random filler

Format:

YES|number|memory

Number:
1 = small
5 = useful
10 = legendary lore

or:

NO
"""
            },
            {
                "role": "user",
                "content": text
            }
        ],
        temperature=0.2
    )

    result = response.choices[0].message.content.strip()

    print("Memory:", result)

    if not result.startswith("YES"):
        return

    try:

        parts = result.split("|")

        importance = int(parts[1])

        memory = parts[2]

        database.save_memory(
            memory,
            importance
        )

    except Exception as e:

        print(
            "Memory failed:",
            e
        )


async def typing_loop(room_id):

    while True:

        try:

            await client.room_typing(
                room_id,
                timeout=10000
            )

            await asyncio.sleep(5)

        except asyncio.CancelledError:

            break


async def send_message(room_id, text):
    """Send a message as Greg and record it for history and replies."""
    response = await client.room_send(
        room_id,
        "m.room.message",
        {
            "msgtype": "m.text",
            "body": text
        }
    )

    if isinstance(response, ErrorResponse):
        print("Send error:", response)
        return None

    event_id = getattr(response, "event_id", None)

    if not event_id:
        print("Send error: no event_id returned")
        return None

    # Remember the event so replies to Greg can wake him up
    database.save_greg_event(event_id)

    # Greg's own replies become assistant messages in his history
    database.save_message(room_id, "greg", text)

    return event_id


async def clear_inactive_sessions():
    """Remove old Matrix device sessions left behind by earlier runs."""
    print("🗑️ Checking for inactive sessions...")

    response = await client.devices()

    if isinstance(response, DevicesError):

        print(
            "Devices error:",
            response
        )

        return

    current = client.device_id

    cutoff = datetime.now() - SESSION_MAX_AGE

    inactive = [
        device.id
        for device in response.devices
        if device.id != current
        and device.last_seen_date is not None
        and device.last_seen_date < cutoff
    ]

    if not inactive:

        print(
            "✅ No inactive sessions to clear"
        )

        return

    print(
        "🗑️ Removing inactive sessions:",
        inactive
    )

    # delete_devices uses user-interactive auth. The first call comes back
    # with a 401 that carries a session id, which must be echoed back along
    # with the credentials or the homeserver rejects the attempt.
    auth = await client.delete_devices(inactive)

    if not isinstance(auth, DeleteDevicesAuthResponse):

        print(
            "Delete devices error:",
            auth
        )

        return

    supported = any(
        "m.login.password" in flow.get("stages", [])
        for flow in auth.flows
    )

    if not supported:

        print(
            "Password auth not offered by server:",
            auth.flows
        )

        return

    result = await client.delete_devices(
        inactive,
        auth={
            "type": "m.login.password",
            "session": auth.session,
            "identifier": {
                "type": "m.id.user",
                "user": MATRIX_USER,
            },
            "password": MATRIX_PASSWORD,
        }
    )

    if isinstance(result, DeleteDevicesResponse):

        print(
            "✅ Inactive sessions cleared"
        )

    else:

        print(
            "Delete devices failed:",
            result
        )


def is_new_event(event_id):
    """Track handled event_ids so a redelivered event is only processed once."""
    if event_id in PROCESSED_EVENTS:
        return False

    PROCESSED_EVENTS.add(event_id)
    PROCESSED_ORDER.append(event_id)

    # Drop the oldest ids once the buffer is full
    while len(PROCESSED_EVENTS) > PROCESSED_ORDER.maxlen:
        PROCESSED_EVENTS.discard(PROCESSED_ORDER.popleft())

    return True


async def turn_worker():
    """Answer triggered messages one turn at a time, coalescing bursts."""
    while True:

        try:

            # The first message of the turn
            turns = [await turn_queue.get()]

            room_id = turns[0][0]

            # Collect messages that arrive during the debounce window so a
            # simultaneous burst is answered once instead of per-message.
            loop = asyncio.get_running_loop()
            deadline = loop.time() + TURN_DEBOUNCE

            while True:

                remaining = deadline - loop.time()

                if remaining <= 0:
                    break

                try:

                    turns.append(
                        await asyncio.wait_for(
                            turn_queue.get(),
                            remaining
                        )
                    )

                except asyncio.TimeoutError:

                    break

            typing_task = asyncio.create_task(
                typing_loop(room_id)
            )

            try:

                answer = await ask_ai(
                    room_id,
                    turns
                )

                await send_message(
                    room_id,
                    answer
                )

                # Let Greg's memory system see everything that happened
                recent = "\n".join(
                    f"{username}: {prompt}"
                    for _, username, prompt, _ in turns
                )

                await check_memory(
                    f"{recent}\nGreg: {answer}"
                )

            except Exception as e:

                print(
                    "AI error:",
                    e
                )

            finally:

                typing_task.cancel()

        except Exception as e:

            # Keep the worker alive if a single turn misbehaves
            print(
                "Turn error:",
                e
            )

            await asyncio.sleep(1)


async def handle_admin_command(room_id, text):
    """Run an owner-only utility command.

    Returns True if the message was a command that was handled, so the
    caller can skip normal reply processing.
    """
    parts = text.split(maxsplit=1)
    command = parts[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""

    if command == "!save_to_memory":

        if not argument:

            await send_message(
                room_id,
                "need something to save, boss"
            )

            return True

        # Explicit saves are treated as top-tier memories
        database.save_memory(argument, 10)

        print(
            "🧠 Owner saved to memory:",
            argument
        )

        await send_message(
            room_id,
            f"✅ saved to memory: {argument}"
        )

        return True

    if command == "!bypass_sys_prompt":

        if not argument:

            await send_message(
                room_id,
                "bypass what? give me text"
            )

            return True

        # Raw model call: no Greg system prompt, no history, no memories
        response = await ai.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "user",
                    "content": argument
                }
            ],
            temperature=0.7
        )

        answer = response.choices[0].message.content

        print(
            "🔓 Owner bypassed system prompt"
        )

        await send_message(
            room_id,
            answer
        )

        return True

    if command == "!list_memory":

        rows = database.list_memories()

        if not rows:

            await send_message(
                room_id,
                "no memories stored"
            )

            return True

        lines = [
            f"[{memory_id}] ({importance}) {memory}"
            for memory_id, memory, importance in rows
        ]

        print(
            f"🧠 Owner listed {len(rows)} memories"
        )

        await send_message(
            room_id,
            "\n".join(lines)
        )

        return True

    if command == "!remove_from_memory":

        if not argument:

            await send_message(
                room_id,
                "remove what? give me a memory id or text"
            )

            return True

        # A plain number is treated as a memory id, anything else as text
        try:

            identifier = int(argument)

        except ValueError:

            identifier = argument

        removed = database.delete_memory(identifier)

        if removed:

            await send_message(
                room_id,
                f"✅ removed {removed} matching memory"
            )

        else:

            await send_message(
                room_id,
                "no matching memory found"
            )

        return True

    return False


async def message_handler(room, event):

    if not isinstance(event, RoomMessageText):
        return

    # Skip Greg's own messages (already recorded when he sent them)
    if event.sender == MATRIX_USER:
        return

    # Skip events that were already handled (homeserver redelivery)
    if not is_new_event(event.event_id):
        return

    username = username_of(event.sender)

    text = event.body.strip()

    # Greg stores every message; the row id lets us keep this message out
    # of history so it isn't shown to the model twice.
    message_id = database.save_message(
        room.room_id,
        username,
        text
    )

    # Owner-only system commands run before normal reply logic
    if event.sender == ADMIN_USER and text.startswith("!"):

        typing_task = asyncio.create_task(
            typing_loop(room.room_id)
        )

        try:

            handled = await handle_admin_command(
                room.room_id,
                text
            )

        finally:

            typing_task.cancel()

        if handled:
            return

    triggered = should_greg_reply(event, text)

    # Beta: when nothing directly triggers Greg, he silently reads
    # the message and decides whether it is aimed at him.
    if not triggered and BETA_READS:

        try:

            triggered = await is_about_greg(text)

        except Exception as e:

            print(
                "Radar error:",
                e
            )

    if not triggered:
        return

    prompt = strip_command(text)

    # Queue the turn; a single worker answers, so overlapping messages
    # are batched instead of producing duplicate replies.
    await turn_queue.put(
        (room.room_id, username, prompt, message_id)
    )


def file_signature(path):
    """Hash a file's contents, or None if it can't be read."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


async def control_loop():
    """Watch the control file for commands from the admin console."""
    while True:

        await asyncio.sleep(2)

        try:

            with open(CONTROL_FILE) as f:
                command = f.read().strip()

        except OSError:

            continue

        if command == "reboot":

            print(
                "🔄 Reboot requested — restarting Greg"
            )

            try:

                os.remove(CONTROL_FILE)

            except OSError:

                pass

            sys.stdout.flush()

            # Exit cleanly; the systemd unit's Restart=always
            # spawns a fresh copy with the new code
            os._exit(0)


async def hot_reload_loop():
    """Watch source files and exit when one changes so systemd restarts."""
    known = {
        path: file_signature(path)
        for path in WATCH_FILES
    }

    while True:

        await asyncio.sleep(2)

        for path in WATCH_FILES:

            signature = file_signature(path)

            # Ignore files that were missing when Greg started
            if known.get(path) is None:
                continue

            if signature != known[path]:

                print(
                    f"🔄 Hot reload: {path} changed — restarting Greg"
                )

                sys.stdout.flush()

                # Exit cleanly; the systemd unit's Restart=always
                # spawns a fresh copy with the new code
                os._exit(0)


async def establish_login():
    """Restore the persistent session, falling back to a fresh login."""
    session = load_session()

    if session:

        user_id, device_id, token = session

        client.restore_login(
            user_id,
            device_id,
            token
        )

        check = await client.whoami()

        if isinstance(check, WhoamiResponse):

            print(
                f"🔁 Restored session: {check.user_id} "
                f"({check.device_id})"
            )

            return True

        print(
            "⚠️ Saved session expired, logging in again"
        )

    login = await client.login(
        MATRIX_PASSWORD,
        device_name="greg-bot"
    )

    if isinstance(login, LoginError):

        if "hard device limit" in str(login):

            print(
                "🚫 Device limit reached. Sign out old sessions from the "
                "matrix.org account page (or Element settings), then "
                "restart Greg. After that, Greg will keep a single "
                "persistent session."
            )

        print(
            "Login failed:",
            login
        )

        return False

    # Never log the full response: it contains the access token
    print(
        f"🔑 Logged in as {login.user_id}, device: {login.device_id}"
    )

    save_session(
        login.user_id,
        login.device_id,
        login.access_token
    )

    print(
        "✅ Session saved for next start"
    )

    return True


async def main():

    setup_log_file()

    database.setup()

    if not await establish_login():
        return

    print("🤖 Greg online")

    if CLEAR_SESSIONS:

        await clear_inactive_sessions()

    await client.sync(
        timeout=0
    )

    client.add_event_callback(
        message_handler,
        RoomMessageText
    )

    # Single worker serializes and coalesces all replies
    asyncio.create_task(
        turn_worker()
    )

    # The admin console can drop a "reboot" file here at any time
    asyncio.create_task(
        control_loop()
    )

    if HOT_RELOAD:

        print("🔄 Hot reload enabled")

        asyncio.create_task(
            hot_reload_loop()
        )

    if MATRIX_ROOM_ID:

        await send_message(
            MATRIX_ROOM_ID,
            "greg is alive 🤖 memory core restored"
        )

    await client.sync_forever(
        timeout=30000
    )


asyncio.run(main())
