import logging
import random
from typing import Mapping

from cltl.combot.event.bdi import DesireEvent
from cltl.combot.event.emissor import TextSignalEvent
from cltl.combot.infra.config import ConfigurationManager
from cltl.combot.infra.event import Event, EventBus
from cltl.combot.infra.resource import ResourceManager
from cltl.combot.infra.time_util import timestamp_now
from cltl.combot.infra.topic_worker import TopicWorker
from cltl.commons.language_data.sentences import GOODBYE
from cltl_service.emissordata.client import EmissorDataClient
from emissor.representation.scenario import TextSignal

logger = logging.getLogger(__name__)


class KeywordService:
    @classmethod
    def from_config(cls, emissor_client: EmissorDataClient,
                    event_bus: EventBus, resource_manager: ResourceManager, config_manager: ConfigurationManager):
        config = config_manager.get_config("cltl.leolani.keyword")
        topics = {
            "intention_topic": config.get("topic_intention"),
            "desire_topic": config.get("topic_desire"),
            "text_in_topic": config.get("topic_text_in"),
            "text_out_topic": config.get("topic_text_out")
        }

        return cls(topics, emissor_client, event_bus, resource_manager)

    def __init__(self, topics: Mapping[str, str],
                 emissor_client: EmissorDataClient, event_bus: EventBus, resource_manager: ResourceManager):
        self._event_bus = event_bus
        self._resource_manager = resource_manager
        self._emissor_client = emissor_client

        self._intention_topic = topics["intention_topic"]
        self._desire_topic = topics["desire_topic"]
        self._text_in_topic = topics["text_in_topic"]
        self._text_out_topic = topics["text_out_topic"]

        self._topic_worker = None

    @property
    def app(self):
        return None

    def start(self, timeout=30):
        self._topic_worker = TopicWorker([self._text_in_topic],
                                         self._event_bus, provides=[self._text_out_topic],
                                         intentions=["chat"], intention_topic=self._intention_topic,
                                         resource_manager=self._resource_manager, processor=self._process,
                                         name=self.__class__.__name__)
        self._topic_worker.start().wait()

    def stop(self):
        if not self._topic_worker:
            pass

        self._topic_worker.stop()
        self._topic_worker.await_stop()
        self._topic_worker = None

    def _process(self, event: Event):
        if self._keyword(event):
            self._event_bus.publish(self._desire_topic, Event.for_payload(DesireEvent(['quit'])))
            self._event_bus.publish(self._text_out_topic, Event.for_payload(self._greeting_payload()))

    def _keyword(self, event):
        if event.metadata.topic == self._text_in_topic:
            return any(event.payload.signal.text.lower() == bye.lower() for bye in GOODBYE)

        return False

    def _greeting_payload(self):
        scenario_id = self._emissor_client.get_current_scenario_id()
        signal = TextSignal.for_scenario(scenario_id, timestamp_now(), timestamp_now(), None,
                                         random.choice(GOODBYE))

        return TextSignalEvent.for_agent(signal)
