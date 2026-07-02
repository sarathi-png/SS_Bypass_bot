import asyncio
import httpx
import re

TIMEOUT = 20.0

async def test_bypasser(name, method, target_url, data, headers, parse_fn):
    print(f"\n=== {name} ===")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as c:
            if method == "POST":
                r = await c.post(target_url, data=data, headers=headers)
            else:
                r = await c.get(target_url, headers=headers)
            print(f"  Status: {r.status_code}, Body: {len(r.text)} chars")
            result = parse_fn(r)
            if result:
                print(f"  RESULT: {result[:200]}")
            else:
                print(f"  No result. First 300: {r.text[:300]}")
    except Exception as e:
        print(f"  ERROR: {e}")


def extract_any_link(resp):
    text = resp.text
    checks = [
        r'<a[^>]*href=["\'](https?://[^\s"\'<>]+)["\']',
        r'"result"\s*:\s*"(https?://[^"]+)"',
        r'"destination"\s*:\s*"(https?://[^"]+)"',
        r'"url"\s*:\s*"(https?://[^"]+)"',
        r'data-url=["\'](https?://[^\s"\'<>]+)["\']',
        r'<div[^>]*class=["\'][^"\']*result[^"\']*["\'][^>]*>.*?<a\s+href=["\'](https?://[^\s"\'<>]+)["\']',
    ]
    for p in checks:
        m = re.search(p, text, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1)
    return None


async def main():
    test_urls = [
        "https://softurl.in/DIH6If0W",
        "https://shortxlinks.in/ul2XI",
        "https://vplink.in/1ec1",
    ]

    for url in test_urls:
        print(f"\n{'='*60}")
        print(f"TESTING: {url}")
        print(f"{'='*60}")
        short = url.split("/")[2]

        # 1 - link-bypass.com (POST form)
        await test_bypasser(
            f"link-bypass.com POST [{short}]",
            "POST",
            "https://link-bypass.com/",
            {"url": url},
            {"Content-Type": "application/x-www-form-urlencoded"},
            extract_any_link,
        )

        # 2 - bypass-links.com (POST form)
        await test_bypasser(
            f"bypass-links.com POST [{short}]",
            "POST",
            "https://bypass-links.com/",
            {"url": url},
            {"Content-Type": "application/x-www-form-urlencoded"},
            extract_any_link,
        )

        # 3 - bypass.tools (different API paths)
        await test_bypasser(
            f"bypass.tools GET [{short}]",
            "GET",
            f"https://bypass.tools/bypass?url={url}",
            None,
            None,
            extract_any_link,
        )

        # 4 - iZen.lol (POST to their API)
        await test_bypasser(
            f"izen.lol POST [{short}]",
            "POST",
            "https://izen.lol/api/bypass",
            {"url": url},
            {"Content-Type": "application/json"},
            extract_any_link,
        )

        await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(main())
