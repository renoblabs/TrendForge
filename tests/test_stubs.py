from trendforge.distribution import PostizProvider, PostRequest
from trendforge.sources import CapCutSource, InstagramSource, TikTokSource, YouTubeSource
import pytest


def test_platform_sources_are_stubbed():
    for cls in (TikTokSource, YouTubeSource, InstagramSource, CapCutSource):
        with pytest.raises(NotImplementedError):
            cls().fetch_candidates()


def test_postiz_provider_is_stubbed():
    provider = PostizProvider(api_key="x")
    with pytest.raises(NotImplementedError):
        provider.list_channels()
    with pytest.raises(NotImplementedError):
        provider.create_post(PostRequest(content="hi", platform="tiktok", channel_id="1"))
