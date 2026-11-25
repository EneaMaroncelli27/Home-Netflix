import json
import requests
from backend.scripts.film import Film
from backend.scripts.urlgetter import URL
from backend.scripts.utils import get_language
from bs4 import BeautifulSoup
import re

HEADERS = {
        "referrer": URL + f'/{get_language()}/archive',
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
    r = session.get(URL + f'/{get_language()}/archive?search={name}',headers=HEADERS)
    if r.status_code != 200:
        return
    soup = BeautifulSoup(r.text,'html.parser')
    data = soup.find('div',id="app")
    json_data = json.loads(data['data-page'])
    films  =  json_data["props"]["titles"]
    films_found : list[Film] = []
    for film in films:
        film["cover"] = find_image_cover(film["images"])
        film["lang"] = get_language()
        films_found.append(Film.model_validate(film))
    
    return films_found

def get_episodes( film : Film, season : int):
    session = requests.Session()
    r = session.get(URL + f'/{film.lang}/titles/{film.id}-{film.slug}' + f'/season-{season}')
    soup = BeautifulSoup(r.text,'html.parser')
    data = soup.find('div',id="app")
    json_data = json.loads(data['data-page'])
    episodes = json_data["props"]["loadedSeason"]["episodes"]
    episodes_found = []
    for n, ep in enumerate(episodes, start=1):
        episode = Film(name=ep["name"], cover=find_image_cover(ep["images"]), slug=film.slug, id=f"{film.id}-{ep['id']}", type=f'episode', seasons_count=0,
                       serie_title=film.name, season=season, episode_n=n, lang=film.lang)
        episodes_found.append(episode)

    return episodes_found