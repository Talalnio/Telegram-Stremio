class BoundedDict(dict):
    def __init__(self, maxlen: int):
        super().__init__()
        self.maxlen = maxlen

    def __setitem__(self, key, value):
        if key not in self and len(self) >= self.maxlen:
            self.pop(next(iter(self)))
        super().__setitem__(key, value)
