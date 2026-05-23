import asyncio
import requests
from bs4 import BeautifulSoup
from firebase_admin import firestore

URL = "https://multiplex.ua/ru/cinema/kyiv/atmosphera"


async def parse_sessions():

    db = firestore.client()
    
    while True:
        try:
            response = requests.get(URL)
            soup = BeautifulSoup(response.text, "html.parser")

            sessions = soup.find_all("div", class_="ns")

            for session in sessions:
                movie = session.get("data-name", "Unknown")

                time_block = session.find("p", class_="time")
                time = time_block.text.strip() if time_block else "??:??"

                tag_block = session.find("p", class_="tag")
                hall = tag_block.text.strip() if tag_block else "Unknown"

                session_id = session.get("data-id", "")

                data = {
                    "movie": movie,
                    "time": time,
                    "hall": hall,
                    "sessionId": session_id,
                }

                db.collection("Sessions").document(session_id).set(data)

            print(f"Updated {len(sessions)} sessions")

        except Exception as e:
            print("Parser error:", e)

        await asyncio.sleep(60)