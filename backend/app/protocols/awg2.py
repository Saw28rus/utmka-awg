class Awg2Manager:
    slug = "awg2"

    async def detect(self, ssh, server):
        return {
            "confidence": "unknown",
            "branch": "needs_review",
            "checks": [],
        }
