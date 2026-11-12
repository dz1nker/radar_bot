import ssl

from aiohttp import ClientSession, TCPConnector
from aiogram.client.session.aiohttp import AiohttpSession


class EnvironmentSession(AiohttpSession):
    """Use the system CA and inherited HTTP(S)_PROXY / NO_PROXY settings."""

    def __init__(self):
        super().__init__()
        self.client = None

    async def create_session(self):
        if self.client is None or self.client.closed:
            self.client = ClientSession(
                connector=TCPConnector(ssl=ssl.create_default_context()), trust_env=True,
            )
        return self.client

    async def close(self):
        if self.client is not None and not self.client.closed:
            await self.client.close()
