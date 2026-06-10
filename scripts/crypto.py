import base64
import logging

logger = logging.getLogger(__name__)

def encode_b64(text: str) -> str:
    """
    将字符串编码为 Base64
    :param text: 原始字符串
    :return: Base64 编码后的字符串
    """
    if not text:
        return ""
    try:
        return base64.b64encode(text.encode('utf-8')).decode('utf-8')
    except Exception as e:
        logger.error(f"Base64 encoding failed: {e}")
        return text

def decode_b64(text: str) -> str:
    """
    将 Base64 字符串解码为明文
    :param text: Base64 编码的字符串
    :return: 解码后的字符串，失败则返回原字符串
    """
    if not text:
        return ""
    try:
        return base64.b64decode(text).decode('utf-8')
    except Exception as e:
        logger.warning(f"Base64 decoding failed (returning raw value): {e}")
        return text
