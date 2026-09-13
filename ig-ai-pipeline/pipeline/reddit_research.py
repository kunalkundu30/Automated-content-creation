"""Optional stage feeding into generate_ideas.py: pulls post titles from
niche subreddits as directional "what's resonating right now" signal.

READ THIS BEFORE ENABLING — Reddit's terms, not a formality:
Reddit's free API tier (100 queries/minute) is explicitly scoped to
non-commercial use. This project is being built for an account that will
eventually run affiliate links and sponsorships — that's a commercial
purpose from the moment it goes live, regardless of whether any revenue has
landed yet. Using the free tier is defensible right now, while the account
is genuinely pre-revenue and this is exploratory research, but it stops
being defensible the moment affiliate links or paid posts go live. At that
point, either remove this module or move to Reddit's paid commercial tier
(reported at roughly $12,000/month as of 2026 — well beyond this project's
budget, which in practice means removing it).

Two other things worth knowing going in:
- Self-service app registration is closed under Reddit's Responsible
  Builder Policy — new OAuth apps go through a manual approval queue
  (reportedly 2-4 weeks), so this isn't instantly available the way the
  Gemini key is.
- This is read-only research signal, never verbatim content. Post titles
  get passed to Gemini as inspiration for topic generation, which must
  paraphrase and reinterpret — never reproduce a Reddit post's title or
  text directly in a caption or script. Also never use this library to
  automate posting or commenting on Reddit itself — that's a separate,
  much brighter ToS line, and a fast way to get an app banned.
"""
import praw

import config

_reddit = None
if config.REDDIT_CLIENT_ID:
    _reddit = praw.Reddit(
        client_id=config.REDDIT_CLIENT_ID,
        client_secret=config.REDDIT_CLIENT_SECRET,
        user_agent=config.REDDIT_USER_AGENT,
    )
    _reddit.read_only = True


def is_enabled() -> bool:
    return _reddit is not None


def fetch_signal_topics(limit_per_subreddit: int = 8) -> list[str]:
    """Returns recent hot post titles across config.REDDIT_SUBREDDITS.
    Returns an empty list (rather than raising) if Reddit isn't configured,
    so the rest of the pipeline works fine while an app registration is
    still sitting in Reddit's approval queue.
    """
    if not is_enabled():
        return []

    titles = []
    for subreddit_name in config.REDDIT_SUBREDDITS:
        subreddit = _reddit.subreddit(subreddit_name)
        for post in subreddit.hot(limit=limit_per_subreddit):
            if not post.stickied:
                titles.append(post.title)
    return titles
