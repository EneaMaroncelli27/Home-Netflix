class Film:
    def __init__(self, title : str, cover : str, slug : str, id : str, type : str, season_c : int = 0):
        self.title =  title
        self.cover = cover
        self.slug = slug
        self.id = id
        self.type = type 
        self.season_c = season_c
        
class Stored:
    def __init__(self, id : str, title : str, type : str, path : str, cover : str, status : str, season_c : int = 0, progress : int = 0):
        self.id = id
        self.title = title
        self.type = type
        self.path = path
        self.cover = cover
        self.status = status
        self.season_c = season_c
        self.progress = progress

