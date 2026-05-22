from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    AGENT_NAME: str = "social"
    MANAGER_URL: str = "http://127.0.0.1:8100"
    API_PORT: int = 8103
    WP_URL: str = "https://pethubonline.com"
    WP_USER: str = "jasonsarah2026"
    WP_APP_PASSWORD: str = "EIul 3KqI 3fY7 yLbk Ltva aPnj"
    HEARTBEAT_INTERVAL: int = 120

    # Facebook
    FB_APP_ID: str = "958132456933125"
    FB_PAGE_ID: str = "1116722411522462"
    FB_PAGE_TOKEN: str = "EAANnapiZBRwUBRvOob7rYqJKmWPWR4t5zlZCn24FrCMIh4n1j1GMYl8aUDQDQuMoOuRNllFZCVlInFvJa9CzPww6SuzZB4YVEqFMhO1PTrMFEJaYhAhcmgHoNM7CfACK7b1hjk7sfsxzuTyPjFlzKlFBeMP6TZCsrKO2NjHPkUm9ZBKFiqJZCkx2HBAbWnvT08BZBUqo"
    FB_GRAPH_URL: str = "https://graph.facebook.com/v21.0"

    # Instagram
    IG_ACCOUNT_ID: str = "17841448517316920"
    IG_USERNAME: str = "pethubonline1"

    # OpenAI
    OPENAI_API_KEY: str = ""

    # Data persistence
    DB_PATH: str = "/var/lib/freelancer/projects/40416335/social-agent/data/social_data.json"

    # Scheduling (UK times)
    MORNING_HOUR: int = 9   # 9am Europe/London
    EVENING_HOUR: int = 18  # 6pm Europe/London
    ENGAGEMENT_INTERVAL_HOURS: int = 6

    class Config:
        env_file = ".env"


settings = Settings()
