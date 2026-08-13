class DmfError(Exception):
    """DMF 系统基础异常。"""
    pass


class DmfClientError(DmfError):
    """DMF 查询接口异常。"""
    pass


class CaptchaError(DmfError):
    """验证码接口异常。"""
    pass