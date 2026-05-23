import asyncio
import requests
from bs4 import BeautifulSoup

import firebase_admin
from firebase_admin import credentials, firestore

import json
import os


URL = "https://multiplex.ua/ru/cinema/kyiv/atmosphera"


async def parse_sessions():

    # 🔥 Firebase init
    if not firebase_admin._apps:

        cred_dict = json.loads(
            os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"]
        )

        cred = credentials.Certificate(cred_dict)

        firebase_admin.initialize_app(cred)

    db = firestore.client()

    while True:

        try:

            print("Loading sessions page...")

            response = requests.get(
                URL,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36"
                    )
                },
                timeout=30,
            )

            print("Status code:", response.status_code)

            soup = BeautifulSoup(response.text, "html.parser")

            sessions = soup.find_all("div", class_="ns")

            print("FOUND SESSIONS:", len(sessions))

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

                print("Saved:", movie, time, hall)

            print(f"Updated {len(sessions)} sessions")

        except Exception as e:

            print("Parser error:", e)

        await asyncio.sleep(60)