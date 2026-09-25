import logging
import random
import string
from typing import Mapping, List

from cltl.combot.event.bdi import DesireEvent
from cltl.combot.event.emissor import TextSignalEvent
from cltl.combot.infra.config import ConfigurationManager
from cltl.combot.infra.event import Event, EventBus
from cltl.combot.infra.event.util import extract_scenario_id
from cltl.combot.infra.resource import ResourceManager
from cltl.combot.infra.time_util import timestamp_now
from cltl.combot.infra.topic_worker import TopicWorker
from cltl.commons.language_data.sentences import GOODBYE
from emissor.representation.scenario import TextSignal

logger = logging.getLogger(__name__)

# Both punctuation and whitespace, so that "Bye! " and " bye" match the keyword
# "Bye". Trailing whitespace is routine in ASR transcripts and chat input, and
# str.strip(string.punctuation) alone does not remove it.
_STRIPPED = string.punctuation + string.whitespace


def _configured(config, key: str, default: List[str]) -> List[str]:
    """A comma-separated setting as a list, or *default* when the key is absent.

    Blank entries are dropped: a stray comma ("Bye,,See you") would otherwise
    contribute "", which strips to "" and so matches every punctuation-only
    utterance. A present-but-empty value is deliberately not the default — it
    is how the shipped configs spell "off".
    """
    if key not in config:
        return list(default)

    return [value for value in config.get(key, multi=True) if value.strip()]


class KeywordService:
    @classmethod
    def from_config(cls, event_bus: EventBus, resource_manager: ResourceManager, config_manager: ConfigurationManager):
        config = config_manager.get_config("cltl.keyword")
        topics = {
            "intention_topic": config.get("topic_intention"),
            "desire_topic": config.get("topic_desire"),
            "text_in_topic": config.get("topic_text_in"),
            "text_out_topic": config.get("topic_text_out")
        }

        intentions = _configured(config, "intentions", default=[])
        keywords = _configured(config, "keywords", default=GOODBYE)
        # Absent falls back to GOODBYE; present-but-empty means say nothing.
        greetings = _configured(config, "greetings", default=GOODBYE)

        if not keywords:
            raise ValueError(
                "[cltl.keyword] keywords is empty: the service would subscribe and "
                "never match anything. Remove the setting to use the default "
                "goodbyes, or disable the service.")

        return cls(keywords, intentions, topics, event_bus, resource_manager, greetings=greetings)

    def __init__(self, keywords: List[str], intentions: List[str], topics: Mapping[str, str],
                 event_bus: EventBus, resource_manager: ResourceManager,
                 greetings: List[str] = None):
        self._event_bus = event_bus
        self._resource_manager = resource_manager

        self._intention_topic = topics["intention_topic"]
        self._desire_topic = topics["desire_topic"]
        self._text_in_topic = topics["text_in_topic"]
        self._text_out_topic = topics["text_out_topic"]

        self._intentions = intentions
        self._keywords = keywords
        self._greetings = GOODBYE if greetings is None else greetings

        self._topic_worker = None

    @property
    def app(self):
        return None

    def start(self, timeout=30):
        self._topic_worker = TopicWorker([self._text_in_topic],
                                         self._event_bus, provides=[self._text_out_topic],
                                         intentions=self._intentions, intention_topic=self._intention_topic,
                                         resource_manager=self._resource_manager, processor=self._process,
                                         name=self.__class__.__name__)
        self._topic_worker.start().wait()

    def stop(self):
        if not self._topic_worker:
            return

        self._topic_worker.stop()
        self._topic_worker.await_stop()
        self._topic_worker = None

    def _process(self, event: Event):
        if self._keyword(event):
            self._event_bus.publish(self._desire_topic, Event.for_payload(DesireEvent(['quit']), source=event))
            # The quit desire is unconditional; only the farewell is optional.
            if self._greetings:
                scenario_id = extract_scenario_id(event)
                greeting_payload = self._greeting_payload(scenario_id)
                self._event_bus.publish(self._text_out_topic, Event.for_payload(greeting_payload, source=event))

    def _keyword(self, event):
        if event.metadata.topic == self._text_in_topic:
            text = event.payload.signal.text.lower().strip(_STRIPPED)

            return any(text == keyword.lower().strip(_STRIPPED) for keyword in self._keywords)

        return False

    def _greeting_payload(self, scenario_id):
        signal = TextSignal.for_scenario(scenario_id, timestamp_now(), timestamp_now(), None,
                                         random.choice(self._greetings))

        return TextSignalEvent.for_agent(signal)
