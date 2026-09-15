class Film:
    def __init__(self, title : str, cover : str, slug : str, id : str, type : str, season_c : int = 0,
                 series_title : str | None = None, season : int = 0, episode_n : int = 0):
        self.title =  title
        self.cover = cover
        self.slug = slug
        self.id = id
        self.type = type 
        self.season_c = season_c
        # Only set for episodes: the series they belong to, and where they sit in it.
        # A film leaves these empty and the shelf treats it as its own entry.
        self.series_title = series_title
        self.season = season
        self.episode_n = episode_n
        
class Stored:
    def __init__(self, id : str, title : str, type : str, path : str, cover : str, status : str, season_c : int = 0, progress : int = 0,
                 series_title : str | None = None, season : int = 0, episode_n : int = 0):
        self.id = id
        self.title = title
        self.type = type
        self.path = path
        self.cover = cover
        self.status = status
        self.season_c = season_c
        self.progress = progress
        self.series_title = series_title
        self.season = season
        self.episode_n = episode_n
