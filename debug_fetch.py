# debug_all.py  — one script to fetch the main profile + all section detail pages
import os
from curl_cffi import requests as cffi
from dotenv import load_dotenv

load_dotenv()
LI_AT = os.getenv("LINKEDIN_LI_AT", "")
JSESSIONID = os.getenv("LINKEDIN_JSESSIONID", "")

# ---- change this to any profile you want to test ----
SLUG = "sinha-shaurya"

MOBILE_UA = ("Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36")
HEADERS = {"user-agent": MOBILE_UA, "accept-language": "en-US,en;q=0.9"}
COOKIES = {"li_at": LI_AT, "JSESSIONID": JSESSIONID}

print(f"li_at length: {len(LI_AT)} (should be ~150+) | JSESSIONID: {JSESSIONID[:12]}...\n")


def fetch(url):
    return cffi.get(url, headers=HEADERS, cookies=COOKIES,
                    impersonate="chrome", allow_redirects=True,
                    max_redirects=5, timeout=25)


def report(label, r, filename):
    is_mwlite = "p_mwlite" in r.text
    has_data = ("list-item-heading" in r.text
                or "entity-lockup" in r.text
                or "heading-large" in r.text
                or "skill-item" in r.text)
    print(f"{label:16} status={r.status_code} len={len(r.text):>7} "
          f"mwlite={is_mwlite} hasData={has_data}  -> {filename}")
    with open(filename, "w", encoding="utf-8") as f:
        f.write(r.text)


# ---- 1) main profile page ----
try:
    r = fetch(f"https://www.linkedin.com/in/{SLUG}/")
    report("PROFILE", r, "profile.html")
except Exception as e:
    print("PROFILE          ERROR:", e)

# ---- 2) experience detail (the one you asked for) ----
try:
    r = fetch(f"https://www.linkedin.com/in/{SLUG}/details/experience")
    report("experience", r, "exp_detail.html")
except Exception as e:
    print("experience       ERROR:", e)

# ---- 3) all other section detail pages ----
SECTIONS = ["education", "projects", "skills",
            "courses", "honors", "languages", "certifications"]

for sec in SECTIONS:
    try:
        r = fetch(f"https://www.linkedin.com/in/{SLUG}/details/{sec}")
        report(sec, r, f"details_{sec}.html")
    except Exception as e:
        print(f"{sec:16} ERROR: {e}")

print("\nDone. Open the files where hasData=True and check the content.")