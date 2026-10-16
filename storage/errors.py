class StaleRequestError(Exception):
    """Заявку тим часом змінив хтось інший (захист від одночасних змін: version у заявці).

    current — актуальна заявка (якщо відома), щоб пояснити користувачу, хто й коли її змінив.
    """

    def __init__(self, current=None):
        super().__init__("Заявку тим часом змінили")
        self.current = current
