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
    FB_PAGE_TOKEN: str = "EAANnapiZBRwUBRnGvBW38grDQwLOf95fc6mEWdI99ZCIUXNEMH9hdnNd592UyO1wDtK0MrhXDwo14q8bewy3m7M6V0lnFc6EqXlpUEVvRcmDDM9lZCatQPeZBZAsxp1ZABg0noSyphUbBONAnOQPdZAU7PL8ZCenZCB0hvsa5lf5vmTZC1QQT9XvaeE9faZBRr9VGTCsyZB4dYkGZCXNinnzQtpIn21J8ZBwkDrH6uuqPjVovjVwwZD"
    FB_GRAPH_URL: str = "https://graph.facebook.com/v21.0"

    # Instagram
    IG_ACCOUNT_ID: str = "17841448517316920"
    IG_USERNAME: str = "pethubonline1"

    # Data persistence
    DB_PATH: str = "/var/lib/freelancer/projects/40416335/social-agent/data/social_data.json"

    # Scheduling (UK times)
    MORNING_HOUR: int = 9   # 9am Europe/London
    EVENING_HOUR: int = 18  # 6pm Europe/London
    ENGAGEMENT_INTERVAL_HOURS: int = 6

    class Config:
        env_file = ".env"


settings = Settings()
