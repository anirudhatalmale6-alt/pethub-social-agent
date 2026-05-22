import logging
import httpx
from config import settings

logger = logging.getLogger("social-agent.manager")

AGENT_ID = 4  # social agent ID in the manager database


async def register_agent():
    """Register the social agent with the manager if it doesn't exist."""
    try:
        async with httpx.AsyncClient() as client:
            # Check if agent already exists by name
            resp = await client.get(f"{settings.MANAGER_URL}/api/agents", timeout=5)
            if resp.status_code == 200:
                agents = resp.json()
                for agent in agents:
                    if agent["name"] == settings.AGENT_NAME:
                        global AGENT_ID
                        AGENT_ID = agent["id"]
                        logger.info(f"Social agent found with id={AGENT_ID}")
                        return AGENT_ID

            # Create the agent
            resp = await client.post(
                f"{settings.MANAGER_URL}/api/agents",
                json={
                    "name": "social",
                    "display_name": "Social Media Agent",
                    "description": "Automated social media posting and engagement tracking for Facebook and Instagram",
                    "agent_type": "social",
                    "endpoint_url": f"http://127.0.0.1:{settings.API_PORT}",
                    "health_check_url": f"http://127.0.0.1:{settings.API_PORT}/api/status",
                    "config": {
                        "platforms": ["facebook", "instagram"],
                        "posting_times": ["09:00 UK", "18:00 UK"],
                    },
                },
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                AGENT_ID = data["id"]
                logger.info(f"Social agent registered with id={AGENT_ID}")
                return AGENT_ID
    except Exception as e:
        logger.error(f"Agent registration failed: {e}")
    return AGENT_ID


async def heartbeat(status: str = "active", metrics: dict = None):
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{settings.MANAGER_URL}/api/heartbeat",
                json={
                    "agent_name": settings.AGENT_NAME,
                    "status": status,
                    "metrics": metrics or {},
                },
                timeout=5,
            )
    except Exception as e:
        logger.error(f"Heartbeat failed: {e}")


async def create_task(
    title: str, task_type: str, input_data: dict = None, priority: str = "normal"
):
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.MANAGER_URL}/api/tasks",
                json={
                    "agent_id": AGENT_ID,
                    "title": title,
                    "task_type": task_type,
                    "priority": priority,
                    "input_data": input_data or {},
                },
                timeout=5,
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.error(f"Create task failed: {e}")
    return None


async def update_task(
    task_id: int, status: str, output_data: dict = None, error_message: str = None
):
    try:
        async with httpx.AsyncClient() as client:
            payload = {"status": status}
            if output_data:
                payload["output_data"] = output_data
            if error_message:
                payload["error_message"] = error_message
            await client.patch(
                f"{settings.MANAGER_URL}/api/tasks/{task_id}",
                json=payload,
                timeout=5,
            )
    except Exception as e:
        logger.error(f"Update task failed: {e}")


async def update_kpi(kpi_name: str, value: float):
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{settings.MANAGER_URL}/api/kpis", timeout=5)
            if resp.status_code == 200:
                kpis = resp.json()
                for kpi in kpis:
                    if kpi["name"] == kpi_name:
                        await client.post(
                            f"{settings.MANAGER_URL}/api/kpis/{kpi['id']}/record",
                            json={"value": value},
                            timeout=5,
                        )
                        return
    except Exception as e:
        logger.error(f"Update KPI failed: {e}")


async def log_message(level: str, message: str):
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{settings.MANAGER_URL}/api/logs",
                params={
                    "agent_name": settings.AGENT_NAME,
                    "level": level,
                    "message": message,
                },
                timeout=5,
            )
    except Exception:
        pass


async def send_alert(title: str, message: str, severity: str = "info"):
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{settings.MANAGER_URL}/api/alerts/test",
                timeout=5,
            )
    except Exception:
        pass
