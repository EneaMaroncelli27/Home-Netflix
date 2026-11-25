from pydantic import BaseModel, ConfigDict

class Media(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    id : str
    name : str
    serie_title : str | None = None
    type : str
    cover : str | None = None
    seasons_count : int
    lang : str
    episode_n : int = 0
    season : int = 0


class Film(Media): # Film returned by the requests and used for the downloads
    slug : str
    
class Stored(Media): # Films showed in the shelf
    path : str
    status : str
    progress : int
