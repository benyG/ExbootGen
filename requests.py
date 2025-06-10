class RequestException(Exception):
    pass

def post(*args, **kwargs):
    raise RequestException("requests.post stub called in test")

def get(*args, **kwargs):
    raise RequestException("requests.get stub called in test")
