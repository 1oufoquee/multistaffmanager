import asyncio
from bs4 import BeautifulSoup

import firebase_admin
from firebase_admin import credentials, firestore

from playwright.async_api import async_playwright

import json
import os


URL = "https://multiplex.ua/ru/cinema/kyiv/atmosphera"


async def parse_sessions():

    # Firebase init
    if not firebase_admin._apps:

        cred_dict = json.loads(
            os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"]
        )

        cred = credentials.Certificate(cred_dict)

        firebase_admin.initialize_app(cred)

    db = firestore.client()

    while True:

        try:

            print("Launching browser...")

            async with async_playwright() as p:

                browser = await p.chromium.launch(
                    headless=True
                )

                page = await browser.new_page()

                await page.goto(URL)

                # ждём загрузку сеансов
                await page.wait_for_selector(
                    "div.ns",
                    timeout=15000
                )

                html = await page.content()

                await browser.close()

            soup = BeautifulSoup(html, "html.parser")

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