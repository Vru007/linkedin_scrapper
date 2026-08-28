"""
LinkedIn Profile API — pure-HTTP, reverse-engineered via LinkedIn's mobile (mwlite) surface.

GET /api/v1/profile?url=<linkedin profile url>   (header: x-api-key)
 -> fetch mwlite SSR HTML (mobile UA + session cookies) -> parse -> structured JSON.
All sections come from the single main profile page (mwlite has no detail pages).
"""

import os
import re
from datetime import datetime, timezone
from typing import Optional

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel
from fastapi.responses import FileResponse

load_dotenv()

LI_AT = os.getenv("LINKEDIN_LI_AT", "")
JSESSIONID = os.getenv("LINKEDIN_JSESSIONID", "")
API_KEY = os.getenv("API_KEY", "")

MOBILE_UA = ("Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36")

app = FastAPI(title="LinkedIn Profile API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
class Position(BaseModel):
    title: Optional[str] = None
    dateRange: Optional[str] = None
    duration: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None


class Experience(BaseModel):
    company: Optional[str] = None
    companyUrl: Optional[str] = None
    companyLogoUrl: Optional[str] = None
    totalDuration: Optional[str] = None      # populated for grouped (multi-role) companies
    positions: list[Position] = []


class Education(BaseModel):
    school: Optional[str] = None
    schoolUrl: Optional[str] = None
    degree: Optional[str] = None
    dateRange: Optional[str] = None
    logoUrl: Optional[str] = None


class Honor(BaseModel):
    name: Optional[str] = None
    issuer: Optional[str] = None
    date: Optional[str] = None


class ContactItem(BaseModel):
    type: Optional[str] = None
    value: Optional[str] = None


class Profile(BaseModel):
    profileUrl: str
    vanityName: Optional[str] = None
    memberUrn: Optional[str] = None
    name: Optional[str] = None
    headline: Optional[str] = None
    location: Optional[str] = None
    connections: Optional[str] = None
    currentCompany: Optional[str] = None
    about: Optional[str] = None
    profileImageUrl: Optional[str] = None
    backgroundImageUrl: Optional[str] = None
    experience: list[Experience] = []
    education: list[Education] = []
    skills: list[str] = []
    projects: list[dict] = []
    courses: list[str] = []
    honors: list[Honor] = []
    languages: list[str] = []
    certifications: list[Honor] = []
    contact: list[ContactItem] = []
    retrievedAt: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def clean(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    return re.sub(r"\s+", " ", text).strip() or None


def extract_slug(url: str) -> Optional[str]:
    m = re.search(r"linkedin\.com/(?:mwlite/profile/)?in/([^/?#]+)", url, re.I)
    return m.group(1) if m else None


def is_linkedin_url(url: str) -> bool:
    return bool(re.match(r"^https?://([a-z0-9-]+\.)?linkedin\.com/", url, re.I))


def img_url(el):
    if not el:
        return None
    return el.get("data-delayed-url") or el.get("src")


def parse_date_range(div):
    """(dateRange, duration) from an mwlite date container."""
    if not div:
        return None, None
    date_parts, duration = [], None
    for span in div.find_all("span"):
        cls = span.get("class") or []
        txt = clean(span.get_text(" ", strip=True))
        if not txt or "dot-separator" in cls:
            continue
        if "body-small" in cls:
            date_parts.append(txt)
        elif not cls:
            duration = txt
    date_range = re.sub(r"\s*-\s*", " - ", " ".join(date_parts)).strip() if date_parts else None
    return date_range, duration


# --------------------------------------------------------------------------- #
# Fetcher
# --------------------------------------------------------------------------- #
def fetch_mwlite_html(slug: str, li_at: str, jsessionid: str):
    url = f"https://www.linkedin.com/in/{slug}/"
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "user-agent": MOBILE_UA,
    }
    cookies = {"li_at": li_at, "JSESSIONID": jsessionid}
    return cffi.get(url, headers=headers, cookies=cookies,
                    impersonate="chrome", allow_redirects=True,
                    max_redirects=5, timeout=25)

def logo_url(el):
    """Return a real logo URL, or None for LinkedIn's ghost placeholder images."""
    u = img_url(el)
    if u and "static.licdn.com" in u:   # ghost/placeholder, not a real logo
        return None
    return u



# --------------------------------------------------------------------------- #
# Section parsers
# --------------------------------------------------------------------------- #
def parse_header(soup):
    pac = soup.select_one(".profile-action-container")
    member_urn = pac.get("data-member-urn") if pac else None
    vanity = (pac.get("data-vanity-name") if pac else None)

    name_el = soup.select_one("h1.heading-large")
    name = clean(name_el.get_text()) if name_el else None

    headline = location = connections = current_company = None
    if name_el:
        header = name_el.parent.parent
        h_el = header.select_one("div.body-small.text-color-text span")
        headline = clean(h_el.get_text()) if h_el else None
        cc = header.select_one("span.member-current-company")
        current_company = clean(cc.get_text()) if cc else None
        low = header.select("div.body-small.text-color-text-low-emphasis")
        if low:
            loc_div = low[-1]
            conn = loc_div.select_one("span.whitespace-nowrap")
            connections = clean(conn.get_text()) if conn else None
            texts = [t.strip() for t in loc_div.find_all(string=True, recursive=False) if t.strip()]
            location = texts[0] if texts else None

    profile_image = img_url(soup.select_one("figure#profile-picture-container img"))
    bg = img_url(soup.select_one(".cover-image-container img"))
    if bg and "static.licdn.com" in bg:
        bg = None
    return name, headline, location, connections, current_company, member_urn, vanity, profile_image, bg


def parse_about(soup):
    el = soup.select_one("section.about-section .description")
    return clean(el.get_text("\n")) if el else None


def parse_experience(soup):
    out = []
    sec = soup.select_one("section.experience-container")
    if not sec:
        return out
    ol = sec.find("ol")
    if not ol:
        return out

    for li in ol.find_all("li", recursive=False):
        roles = li.select("li.role-container")

        # ---- grouped company (multiple roles) ----
        if roles:
            comp_block = li.select_one("div.self-center")
            head = comp_block.select_one(".list-item-heading") if comp_block else None
            dur = comp_block.select_one("div.body-small") if comp_block else None
            comp_link = li.select_one("a[data-tracking-control-name='experience-timeline']")
            positions = []
            for role in roles:
                title = role.select_one(".body-small-bold")
                dr, du = parse_date_range(role.select_one("div.body-small"))
                desc = role.select_one(".description")
                positions.append(Position(
                    title=clean(title.get_text()) if title else None,
                    dateRange=dr, duration=du,
                    description=clean(desc.get_text("\n")) if desc else None,
                ))
            out.append(Experience(
                company=clean(head.get_text()) if head else None,
                companyUrl=(comp_link.get("href") or "").split("?")[0] if comp_link else None,
                companyLogoUrl=logo_url(li.select_one("figure img")),
                totalDuration=clean(dur.get_text()) if dur else None,
                positions=positions,
            ))
            continue

        # ---- single position (with OR without a company link, e.g. Freelance) ----
        content = li.select_one("div.flex-1.self-center")
        if not content:
            continue
        title = content.select_one(".list-item-heading")
        body_smalls = [d for d in content.find_all("div", recursive=False)
                       if "body-small" in (d.get("class") or [])]
        company = clean(body_smalls[0].get_text(" ", strip=True)) if body_smalls else None
        dr, du = parse_date_range(body_smalls[1]) if len(body_smalls) > 1 else (None, None)
        loc = content.select_one(".text-xs")
        desc = content.select_one(".description")
        a = li.select_one("a[data-tracking-control-name='profile-position']")
        out.append(Experience(
            company=company,
            companyUrl=(a.get("href") or "").split("?")[0] if a else None,
            companyLogoUrl=logo_url(li.select_one("figure img")),
            positions=[Position(
                title=clean(title.get_text()) if title else None,
                dateRange=dr, duration=du,
                location=clean(loc.get_text()) if loc else None,
                description=clean(desc.get_text("\n")) if desc else None,
            )],
        ))
    return out

def parse_education(soup):
    out = []
    sec = soup.select_one("section.education-container")
    if not sec:
        return out
    for a in sec.select("a[data-tracking-control-name='view-education']"):
        content = a.select_one("div.flex-1") or a
        school = content.select_one(".list-item-heading")
        degree = content.select_one("div.body-small.text-color-text span")
        dates_div = content.select_one("div.body-small.text-color-text-low-emphasis")
        dr, _ = parse_date_range(dates_div)
        out.append(Education(
            school=clean(school.get_text()) if school else None,
            schoolUrl=(a.get("href") or "").split("?")[0] or None,
            degree=clean(degree.get_text()) if degree else None,
            dateRange=dr,
            logoUrl=img_url(a.select_one("figure img")),
        ))
    return out


def parse_skills(soup):
    return [clean(s.get_text()) for s in soup.select("ol.skills-list li.skill-item span[dir=ltr]") if clean(s.get_text())]


def _accomplishment_items(soup, section_class):
    block = soup.select_one(f"div.{section_class}")
    if not block:
        return []
    rows = []
    for li in block.select("li.sub-list-item"):
        heading = li.select_one(".list-item-heading")
        name = clean(heading.get_text()) if heading else None
        if not name:
            continue
        detail = li.select_one(".body-small.text-color-text-low-emphasis")
        rows.append((name, detail))
    return rows


def parse_languages(soup):
    return [n for n, _ in _accomplishment_items(soup, "languages-section")]


def parse_courses(soup):
    return [n for n, _ in _accomplishment_items(soup, "courses-section")]


def parse_projects(soup):
    out = []
    for name, detail in _accomplishment_items(soup, "projects-section"):
        out.append({"name": name, "detail": clean(detail.get_text(" ", strip=True)) if detail else None})
    return out


def _honor_like(soup, section_class):
    out = []
    for name, detail in _accomplishment_items(soup, section_class):
        issuer = date = None
        if detail:
            spans = detail.select("span[dir=ltr]")
            issuer = clean(spans[0].get_text()) if spans else None
            d = detail.select_one(".date")
            date = clean(d.get_text()) if d else None
        out.append(Honor(name=name, issuer=issuer, date=date))
    return out


def parse_contact(soup):
    out = []
    for li in soup.select("#contact-list li"):
        title = li.select_one(".contact-title")
        val = li.select_one(".contact-value")
        out.append(ContactItem(
            type=clean(title.get_text()) if title else None,
            value=(val.get("href") or clean(val.get_text())) if val else None,
        ))
    return out


def parse_profile(html: str, slug: str) -> Profile:
    soup = BeautifulSoup(html, "lxml")
    (name, headline, location, connections, current_company,
     member_urn, vanity, profile_image, bg) = parse_header(soup)

    return Profile(
        profileUrl=f"https://www.linkedin.com/in/{vanity or slug}",
        vanityName=vanity or slug,
        memberUrn=member_urn,
        name=name,
        headline=headline,
        location=location,
        connections=connections,
        currentCompany=current_company,
        about=parse_about(soup),
        profileImageUrl=profile_image,
        backgroundImageUrl=bg,
        experience=parse_experience(soup),
        education=parse_education(soup),
        skills=parse_skills(soup),
        projects=parse_projects(soup),
        courses=parse_courses(soup),
        honors=_honor_like(soup, "honors-section"),
        languages=parse_languages(soup),
        certifications=_honor_like(soup, "certifications-section"),
        contact=parse_contact(soup),
        retrievedAt=datetime.now(timezone.utc).isoformat(),
    )


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/api/v1/profile", response_model=Profile)
def get_profile(
    url: str = Query(...),
    x_api_key: Optional[str] = Header(None),
    x_li_at: Optional[str] = Header(None),
    x_jsessionid: Optional[str] = Header(None),
):
    # Use caller-supplied tokens if present; otherwise fall back to server .env tokens.
    if x_li_at and x_jsessionid:
        li_at, jsessionid = x_li_at, x_jsessionid
    else:
        if API_KEY and x_api_key != API_KEY:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")
        li_at, jsessionid = LI_AT, JSESSIONID

    if not is_linkedin_url(url):
        raise HTTPException(status_code=400, detail="URL must be a linkedin.com profile URL")
    slug = extract_slug(url)
    if not slug:
        raise HTTPException(status_code=400, detail="Could not extract profile slug from URL")
    if not li_at or not jsessionid:
        raise HTTPException(status_code=400, detail="No LinkedIn session — set your li_at and JSESSIONID in Settings")

    resp = fetch_mwlite_html(slug, li_at, jsessionid)
    final = str(resp.url).lower()
    if resp.status_code in (401, 403, 999) or "authwall" in final or "/login" in final:
        raise HTTPException(status_code=502, detail="LinkedIn session expired or blocked — update your li_at token")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Upstream error: HTTP {resp.status_code}")

    profile = parse_profile(resp.text, slug)
    if not profile.name:
        raise HTTPException(status_code=404, detail="Profile data not found in response")
    return profile

@app.get("/")
def index():
    return FileResponse("static/index.html")
