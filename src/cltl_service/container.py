import json
import logging

from cltl.combot.infra.container import InfraContainer
from cltl.combot.infra.di_container import singleton
from cltl_service.bdi.service import BDIService
from cltl_service.context.service import ContextService
from cltl_service.intentions.init import InitService
from cltl_service.keyword.service import KeywordService

logger = logging.getLogger(__name__)


class ElizaComponentsContainer(InfraContainer):
    """Container for Eliza cognitive component services: BDI, keyword detection, and init intention.

    ContextService is intentionally omitted — it is application-specific and must be added
    by the application layer (see app/docker-app/app.py).
    """

    @property
    @singleton
    def context_service(self) -> ContextService:
        return ContextService.from_config(self.event_bus, self.resource_manager, self.config_manager)

    @property
    @singleton
    def keyword_service(self) -> KeywordService:
        return KeywordService.from_config(self.event_bus, self.resource_manager, self.config_manager)

    @property
    @singleton
    def bdi_service(self) -> BDIService:
        bdi_model = json.loads(self.config_manager.get_config("cltl.bdi").get("model"))
        return BDIService.from_config(bdi_model, self.event_bus, self.resource_manager, self.config_manager)

    @property
    @singleton
    def init_intention(self) -> InitService:
        return InitService.from_config(self.event_bus, self.resource_manager, self.config_manager)

    def start(self):
        logger.info("Start Eliza services")
        super().start()
        self.bdi_service.start()
        self.keyword_service.start()
        self.context_service.start()
        self.init_intention.start()

    def stop(self):
        logger.info("Stop Eliza services")
        self.init_intention.stop()
        self.bdi_service.stop()
        self.keyword_service.stop()
        self.context_service.stop()
        super().stop()
