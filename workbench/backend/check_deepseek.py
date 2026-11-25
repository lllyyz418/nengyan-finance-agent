"""Minimal opt-in connection check. Never uploads reports or prints API secrets."""
import os
from pathlib import Path
from dotenv import load_dotenv


def main():
    load_dotenv(Path(__file__).resolve().parent / '.env', override=False)
    key = os.getenv('DEEPSEEK_API_KEY', '').strip()
    if not key:
        print('尚未填写密钥：请在 backend/.env 中填写 DEEPSEEK_API_KEY 后保存。不要把密钥发到聊天里。')
        return 1
    from openai import OpenAI, APIConnectionError, APIStatusError, AuthenticationError, RateLimitError
    model = os.getenv('DEEPSEEK_MODEL', '').strip() or 'deepseek-flash'
    try:
        client = OpenAI(api_key=key, base_url='https://api.deepseek.com', timeout=30, max_retries=0)
        result = client.chat.completions.create(model=model, max_tokens=32,
            extra_body={'thinking': {'type': 'disabled'}},
            messages=[{'role':'user', 'content':'这是接口连通性测试，请仅回复：连接成功。'}])
        if not result.choices or not (result.choices[0].message.content or '').strip():
            print('服务返回空内容，尚不能确认文字报告生成功能可用。')
            return 1
        print('DeepSeek 连接验证成功。测试未上传财报；新分析任务可使用模型生成报告。')
        return 0
    except AuthenticationError:
        print('密钥验证失败：请在本机核对密钥是否正确、已启用且未失效。')
    except RateLimitError:
        print('接口限流，请稍后重试。密钥是否可用于生成报告仍需验证。')
    except APIConnectionError:
        print('无法连接 DeepSeek，请检查网络或代理；未绕过证书校验。')
    except APIStatusError as error:
        if error.status_code == 402:
            print('服务提示账户余额或额度不足，请在 DeepSeek 平台核对账户状态。')
        elif error.status_code in {400,404,422}:
            print('模型名称或请求参数不可用，请核对 DEEPSEEK_MODEL 与官方文档。')
        else:
            print(f'DeepSeek 服务返回 HTTP {error.status_code}，请稍后重试。')
    except Exception:
        print('连接验证未完成。请检查依赖和配置；为保护密钥不输出原始异常。')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
