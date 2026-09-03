import json
import requests
from backend.scripts.film import Film
from backend.scripts.urlgetter import URL
from bs4 import BeautifulSoup
import re

DATA_PAGE_RE = re.compile(r'data-page="([^"]+)"')

HEADERS = {
        "referrer": URL + '/it/archive',
        "Content-Type":"application/json",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
        "Accept": "text/html, application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.5",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "mode":"cors"
    }


def find_image_cover( images : list ):
    for img in images:
        if img["type"] == "cover":
            return img["filename"]

def search_by_title( name : str ) -> list:
    session = requests.Session()
    r = session.get(URL + f'/it/archive?search={name}',headers=HEADERS)
    if r.status_code != 200:
        return
    soup = BeautifulSoup(r.text,'html.parser')
    data = soup.find('div',id="app")
    json_data = json.loads(data['data-page'])
    films = json_data["props"]["titles"]
    films_found = []
    for f in films:
        film = Film(f["name"], find_image_cover(f["images"]), f["slug"], str(f["id"]), f['type'], f['seasons_count'])
        films_found.append(film)
    
    return films_found

def get_episodes( film : Film, season : int):
    session = requests.Session()
    r = session.get(URL + f'/it/titles/{film.id}-{film.slug}' + f'/season-{season}')
    soup = BeautifulSoup(r.text,'html.parser')
    data = soup.find('div',id="app")
    json_data = json.loads(data['data-page'])
    episodes = json_data["props"]["loadedSeason"]["episodes"]
    episodes_found = []
    for ep in episodes:
        episode = Film(ep["name"], find_image_cover(ep["images"]), film.slug, f"{film.id}-{ep['id']}", f'episode', 0)
        episodes_found.append(episode)

    return episodes_found