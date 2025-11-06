import logging
import random
import re
from queue import Queue
from typing import Mapping

from cltl.combot.event.bdi import DesireEvent
from cltl.combot.event.emissor import TextSignalEvent
from cltl.combot.infra.config import ConfigurationManager
from cltl.combot.infra.event import Event, EventBus
from cltl.combot.infra.resource import ResourceManager
from cltl.combot.infra.time_util import timestamp_now
from cltl.combot.infra.topic_worker import TopicWorker
from cltl.commons.language_data.sentences import GREETING, GOODBYE
from emissor.representation.scenario import TextSignal

logger = logging.getLogger(__name__)


TIMEOUT = 120_000


_GREETINGS = [re.sub('[^a-z]+', '', greeting.lower()) for greeting in GREETING]


class InitService:
    @classmethod
    def from_config(cls, event_bus: EventBus, resource_manager: ResourceManager, config_manager: ConfigurationManager):
        config = config_manager.get_config("cltl.intentions.init")
        topics = {
            "scenario_topic": config.get("topic_scenario"),
            "intention_topic": config.get("topic_intention"),
            "desire_topic": config.get("topic_desire"),
            "text_in_topic": config.get("topic_text_in"),
            "text_out_topic": config.get("topic_text_out"),
            "face_topic": config.get("topic_face"),
        }

        greeting = config.get("greeting")

        return cls(topics, greeting, event_bus, resource_manager)

    def __init__(self, topics: Mapping[str, str], greeting: str,
                 event_bus: EventBus, resource_manager: ResourceManager):
        self._event_bus = event_bus
        self._resource_manager = resource_manager

        self._scenario_topic = topics["scenario_topic"]
        self._intention_topic = topics["intention_topic"]
        self._desire_topic = topics["desire_topic"]
        self._text_in_topic = topics["text_in_topic"]
        self._text_out_topic = topics["text_out_topic"]
        self._face_topic = topics["face_topic"]
        self._greeting = greeting

        self._topic_worker = None

        self._scenario_id = None
        self._speaker_name = None
        self._timeout = None
        self._init_queue = Queue()

    @property
    def app(self):
        return None

    def start(self, timeout=30):
        self._topic_worker = TopicWorker(list(filter(bool, [self._scenario_topic, self._face_topic, self._text_in_topic])),
                                         self._event_bus, provides=[self._text_out_topic],
                                         intentions=["init"], intention_topic=self._intention_topic,
                                         resource_manager=self._resource_manager, processor=self._process,
                                         scheduled=3,
                                         name=self.__class__.__name__)
        self._topic_worker.start().wait()

    def stop(self):
        if not self._topic_worker:
            pass

        self._topic_worker.stop()
        self._topic_worker.await_stop()
        self._topic_worker = None

    def _process(self, event: Event):
        if event and not event.metadata.tenant:
            logger.warning("The BDIService should only run in a tenant context!")

        scheduled_invocation = event is None

        if not self._scenario_id and scheduled_invocation:
            return

        if not scheduled_invocation and event.metadata.topic == self._scenario_topic:
            logger.debug("Set scenario")
            if event.payload.type == "ScenarioStarted":
                self._scenario_id = event.payload.scenario.id
                try:
                    self._speaker_name = event.payload.scenario.context.speaker.name
                except AttributeError:
                    pass
                # Trigger processing of init queue
                self._process(None)
            elif event.payload.type == "ScenarioStopped":
                self._scenario_id = None

            return

        if not self._scenario_id:
            self._init_queue.put(event)
            logger.debug("Waiting for scenario, queued for %s (topic: %s, total: %s)",
                         event.id, event.metadata.topic, len(self._init_queue.queue))
            return

        if scheduled_invocation and not self._init_queue.empty():
            logger.debug("Processing init queue (%s) for scenario %s", len(self._init_queue.queue), self._scenario_id)
            event = self._init_queue.get()

        if not self._greeting:
            # Add scenario id, as it could be a scheduled invocation without event
            init_event = Event.for_scenario_payload(self._scenario_id, DesireEvent(["initialized"]), source=event)
            self._event_bus.publish(self._desire_topic, init_event)
            self._init_queue.queue.clear()
            logger.info("Initialized without greeting")
            return

        timestamp = timestamp_now()

        if (scheduled_invocation or self._face_or_keyword(event)) and not self._timeout:
            greeting = self._get_greeting()
            # Add scenario id, as it could be a scheduled invocation without event
            greeting_event = Event.for_scenario_payload(self._scenario_id, self._create_text_signal_event(greeting), source=event)
            self._event_bus.publish(self._text_out_topic, greeting_event)
            self._timeout = timestamp
            logger.info("Start initialization")
        elif scheduled_invocation and not self._init_queue.empty():
            # Trigger further processing of init queue
            self._process(None)
            return
        elif scheduled_invocation:
            pass
        elif self._timeout and timestamp - self._timeout < TIMEOUT and self._start_utterance(event):
            self._timeout = None
            init_event = Event.for_payload(DesireEvent(["initialized"]), source=event)
            self._event_bus.publish(self._desire_topic, init_event)
            logger.info("Interaction initialized")
        elif self._timeout and timestamp - self._timeout > TIMEOUT:
            self._timeout = None
            goodbye = random.choice(GOODBYE) + " Let me know when you are back."
            goodbye_event = Event.for_payload(self._create_text_signal_event(goodbye), source=event)
            self._event_bus.publish(self._text_out_topic, goodbye_event)
            logger.info("Reset initialization")
        else:
            logger.debug("Unhandled event %s (%s - %s)", event, timestamp, self._timeout)

    def _get_greeting(self) -> str:
        if self._speaker_name:
            custom_greeting = self._greeting.format_map({"name": self._speaker_name})
        else:
            custom_greeting = self._greeting.replace("{name}", "")

        return random.choice(GREETING) + " " + custom_greeting

    def _start_utterance(self, event):
        return event.metadata.topic == self._text_in_topic and "yes" in event.payload.signal.text.lower()

    def _face_or_keyword(self, event):
        if event.metadata.topic == self._face_topic:
            return any(annotation.value
                for mention in event.payload.mentions
                for annotation in mention.annotations)
        if event.metadata.topic == self._text_in_topic:
            utterance = re.sub('[^a-z]+', '', event.payload.signal.text.lower())
            return any(greeting in utterance for greeting in _GREETINGS)

    def _create_text_signal_event(self, text: str):
        signal = TextSignal.for_scenario(self._scenario_id, timestamp_now(), timestamp_now(), None, text)

        return TextSignalEvent.for_agent(signal)
