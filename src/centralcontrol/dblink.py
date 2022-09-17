class DBLink(object):
    """class to manage the link to our database"""

    address = "postgres://"

    def __init__(self, address: str | None = None):
        if address:
            self.address = address
