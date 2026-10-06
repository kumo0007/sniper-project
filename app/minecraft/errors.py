class MinecraftError(Exception):
    """A Minecraft or Microsoft call failed in a way the operator can act on."""

    def __init__(self, message: str, *, kind: str = "error", http_status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.http_status = http_status


class AuthPending(MinecraftError):
    def __init__(self):
        super().__init__("Waiting for Microsoft sign-in.", kind="pending")


class AuthSlowDown(MinecraftError):
    def __init__(self):
        super().__init__("Microsoft asked the sign-in poll to slow down.", kind="slow_down")


class AuthDeclined(MinecraftError):
    def __init__(self):
        super().__init__("Microsoft sign-in was declined.", kind="auth_error")


class AuthExpired(MinecraftError):
    def __init__(self):
        super().__init__("The Microsoft sign-in code expired. Start again.", kind="auth_error")
