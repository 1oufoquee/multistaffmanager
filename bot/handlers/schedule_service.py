import requests
from bs4 import BeautifulSoup
from datetime import datetime

ATMOSFERA_URL = "https://multiplex.ua/cinema/kyiv/atmosphera"


def get_today_sessions():
    try:
        response = requests.get(
            ATMOSFERA_URL,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept-Language": "ru,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml",
            }
            timeout=15,
        )

        response.raise_for_status()

    except Exception as e:
        print(f"[schedule] request error: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    print(response.text[:1000])

    sessions = []

    blocks = soup.select("div.ns")

    for block in blocks:
        try:
            session_id = block.get("data-id", "")
            movie_name = block.get("data-name", "")

            time_tag = block.find("p", class_="time")
            format_tag = block.find("p", class_="tag")

            if not time_tag:
                continue

            start_time = time_tag.text.strip()
            movie_format = format_tag.text.strip() if format_tag else "—"

            sessions.append({
                "sessionId": session_id,
                "movie": movie_name,
                "time": start_time,
                "format": movie_format,
            })

        except Exception as e:
            print(f"[schedule] parse error: {e}")

    return sessions


def get_nearest_session():
    sessions = get_today_sessions()

    if not sessions:
        return None

    now = datetime.now()

    nearest = None
    nearest_diff = None

    for s in sessions:
        try:
            session_time = datetime.strptime(s["time"], "%H:%M")

            session_dt = now.replace(
                hour=session_time.hour,
                minute=session_time.minute,
                second=0,
                microsecond=0,
            )

            diff = (session_dt - now).total_seconds()

            if diff < 0:
                continue

            if nearest is None or diff < nearest_diff:
                nearest = s
                nearest_diff = diff

        except:
            continue

    if nearest:
        nearest["minutesLeft"] = int(nearest_diff // 60)

    return nearest