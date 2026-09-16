# This Python file uses the following encoding: utf-8

CLIENT_X7 = 'x7'
CLIENT_OFFICIAL = 'official'

CLIENT_NAME = {
    CLIENT_X7: '小七版',
    CLIENT_OFFICIAL: '官方版',
}

PACKAGE_CLIENT = {
    'pip.com.xuanyuan.x7sy': CLIENT_X7,
    'com.pip.android.xuanyuan': CLIENT_OFFICIAL,
}

client = CLIENT_X7


def resolve_client(package: str) -> str:
    """
    根据游戏包名解析客户端版本，未知包名按小七版处理
    :param package: 游戏包名
    :return: CLIENT_X7 或 CLIENT_OFFICIAL
    """
    return PACKAGE_CLIENT.get(str(package), CLIENT_X7)


def set_client(package: str) -> str:
    """
    设置全局客户端版本，连接设备时调用
    """
    global client
    client = resolve_client(package)
    return client


def get_client() -> str:
    return client


def get_client_name(client_or_package: str = None) -> str:
    key = client_or_package or client
    if key in CLIENT_NAME:
        return CLIENT_NAME[key]
    return CLIENT_NAME.get(resolve_client(key), str(key))
