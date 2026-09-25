"""Unit tests for KeywordService: the configurable quit keywords and intentions.

Two things are worth pinning here, and neither is obvious from reading the
service alone.

The first is intention gating. ``TopicWorker`` starts *active* when its
intentions list is empty and *inactive* when it is not, so the difference
between ``intentions: eliza`` and an absent setting is the difference between a
keyword that fires only during a conversation and one that fires always. The
service used to hardcode ``intentions=["chat"]``, which no BDI model in this
repo ever produces, so the goodbye keyword was unreachable —
``TestIntentionGating`` is the regression test for that.

The second is the matcher. It compares whole utterances, not substrings, after
stripping punctuation and whitespace from both sides.
"""
import logging
import string
import threading
import unittest
from configparser import ConfigParser
from queue import Empty, Queue

from cltl.combot.event.bdi import DesireEvent, Intention, IntentionEvent
from cltl.combot.event.emissor import TextSignalEvent
from cltl.combot.infra.config.local import LocalConfigurationManager
from cltl.combot.infra.event import Event
from cltl.combot.infra.event.memory import SynchronousEventBus
from cltl.combot.infra.time_util import timestamp_now
from cltl.commons.language_data.sentences import GOODBYE
from emissor.representation.scenario import TextSignal

from cltl_service.keyword.service import KeywordService

INTENTION_TOPIC = "intentionTopic"
DESIRE_TOPIC = "desireTopic"
TEXT_IN_TOPIC = "textInTopic"
TEXT_OUT_TOPIC = "textOutTopic"

TOPICS = {
    "intention_topic": INTENTION_TOPIC,
    "desire_topic": DESIRE_TOPIC,
    "text_in_topic": TEXT_IN_TOPIC,
    "text_out_topic": TEXT_OUT_TOPIC,
}

# Long enough to be reliable on a loaded machine, short enough that a suite of
# negative assertions does not crawl.
TIMEOUT = 1.0
NEGATIVE_TIMEOUT = 0.1


def utterance(text: str, scenario_id: str = None) -> TextSignalEvent:
    signal = TextSignal.for_scenario(scenario_id, timestamp_now(), timestamp_now(), None, text)

    return TextSignalEvent.for_speaker(signal)


def config_manager(section_body: str) -> LocalConfigurationManager:
    """A real LocalConfigurationManager over an in-memory INI section.

    The real one rather than a fake, because ``from_config`` depends on exactly
    the semantics a fake would have to guess at: ``"key" in config`` is
    ``ConfigParser.has_option``, so a present-but-empty ``keywords:`` is *in* the
    config and yields ``[]``, while an absent one falls back to GOODBYE.
    """
    parser = ConfigParser({}, strict=False)
    parser.read_string("[cltl.keyword]\n"
                       f"topic_intention: {INTENTION_TOPIC}\n"
                       f"topic_desire: {DESIRE_TOPIC}\n"
                       f"topic_text_in: {TEXT_IN_TOPIC}\n"
                       f"topic_text_out: {TEXT_OUT_TOPIC}\n"
                       + section_body)

    return LocalConfigurationManager(parser)


class TestFromConfig(unittest.TestCase):
    """Construction only — no topic worker is started."""

    def test_configured_keywords_are_split_and_stripped(self):
        service = KeywordService.from_config(
            SynchronousEventBus(), None, config_manager("keywords: Bye, stop talking ,enough\n"))

        self.assertEqual(["Bye", "stop talking", "enough"], service._keywords)

    def test_absent_keywords_defaults_to_goodbye(self):
        service = KeywordService.from_config(SynchronousEventBus(), None, config_manager(""))

        self.assertEqual(GOODBYE, service._keywords)

    def test_empty_keywords_is_rejected(self):
        """Present-but-empty is a misconfiguration, not a way to disable.

        A service with no keywords subscribes and can never match, which is
        indistinguishable at runtime from one that is working. Failing at
        construction is the only point where it is still visible.
        """
        with self.assertRaises(ValueError) as error:
            KeywordService.from_config(
                SynchronousEventBus(), None, config_manager("keywords:\n"))

        self.assertIn("keywords", str(error.exception))

    def test_keywords_of_only_blanks_is_rejected(self):
        """Same failure by a different route: every entry filtered away."""
        with self.assertRaises(ValueError):
            KeywordService.from_config(
                SynchronousEventBus(), None, config_manager("keywords: ,,\n"))

    def test_blank_keywords_are_dropped(self):
        """A stray comma must not contribute an empty keyword.

        An empty keyword strips to "" and would match every punctuation-only
        utterance, ending conversations at random.
        """
        service = KeywordService.from_config(
            SynchronousEventBus(), None, config_manager("keywords: Bye,, ,See you\n"))

        self.assertEqual(["Bye", "See you"], service._keywords)

    def test_configured_greetings_are_split_and_stripped(self):
        service = KeywordService.from_config(
            SynchronousEventBus(), None, config_manager("greetings: Farewell, So long \n"))

        self.assertEqual(["Farewell", "So long"], service._greetings)

    def test_absent_greetings_defaults_to_goodbye(self):
        service = KeywordService.from_config(SynchronousEventBus(), None, config_manager(""))

        self.assertEqual(GOODBYE, service._greetings)

    def test_empty_greetings_means_say_nothing(self):
        """Unlike keywords, an empty greetings list is a legitimate choice.

        It ends the conversation without a farewell, which is what a headless
        pipeline with no text output wants.
        """
        service = KeywordService.from_config(
            SynchronousEventBus(), None, config_manager("greetings:\n"))

        self.assertEqual([], service._greetings)

    def test_configured_intentions_are_split_and_stripped(self):
        service = KeywordService.from_config(
            SynchronousEventBus(), None, config_manager("intentions: eliza, chat\n"))

        self.assertEqual(["eliza", "chat"], service._intentions)

    def test_absent_intentions_means_always_active(self):
        service = KeywordService.from_config(SynchronousEventBus(), None, config_manager(""))

        self.assertEqual([], service._intentions)

    def test_empty_intentions_means_always_active(self):
        """The other branch of the `"intentions" in config` ternary.

        Distinct from the absent case: this one goes through `get(multi=True)`
        on an empty value. Both must yield [], and this is the branch the
        shipped `intentions:` idiom actually takes.
        """
        service = KeywordService.from_config(
            SynchronousEventBus(), None, config_manager("intentions:\n"))

        self.assertEqual([], service._intentions)

    def test_topics_are_read_from_config(self):
        """The constructor takes topics third, not first.

        A caller that still passes them positionally in the old order would wire
        every topic to the wrong attribute without raising.
        """
        service = KeywordService.from_config(SynchronousEventBus(), None, config_manager(""))

        self.assertEqual(INTENTION_TOPIC, service._intention_topic)
        self.assertEqual(DESIRE_TOPIC, service._desire_topic)
        self.assertEqual(TEXT_IN_TOPIC, service._text_in_topic)
        self.assertEqual(TEXT_OUT_TOPIC, service._text_out_topic)


class KeywordServiceTestCase(unittest.TestCase):
    """Starts a service and captures what it publishes."""

    def setUp(self) -> None:
        self.event_bus = SynchronousEventBus()
        self.service = None
        self.desires = Queue()
        self.replies = Queue()
        self.event_bus.subscribe(DESIRE_TOPIC, self.desires.put)
        self.event_bus.subscribe(TEXT_OUT_TOPIC, self.replies.put)

    def tearDown(self) -> None:
        # Only a started service: KeywordService.stop() dereferences its topic
        # worker unconditionally, so stopping one that never started raises.
        if self.service:
            self.service.stop()

    def start(self, keywords, intentions=(), greetings=None) -> KeywordService:
        self.service = KeywordService(
            list(keywords), list(intentions), TOPICS, self.event_bus, None,
            greetings=None if greetings is None else list(greetings))
        self.service.start()

        return self.service

    def say(self, text: str, scenario_id: str = None) -> None:
        self.event_bus.publish(TEXT_IN_TOPIC, Event.for_payload(utterance(text, scenario_id)))

    def assert_quit(self, message: str = "no quit desire was published") -> Event:
        try:
            event = self.desires.get(timeout=TIMEOUT)
        except Empty:
            self.fail(message)

        self.assertIsInstance(event.payload, DesireEvent)
        self.assertEqual(["quit"], event.payload.achieved)

        return event

    def assert_no_quit(self, message: str = "an unexpected quit desire was published") -> None:
        try:
            event = self.desires.get(timeout=NEGATIVE_TIMEOUT)
        except Empty:
            return
        self.fail(f"{message}: {event.payload}")


class TestLifecycle(KeywordServiceTestCase):
    def test_stop_before_start_is_a_no_op(self):
        """A container that fails part-way through start() still stops cleanly.

        ContextComponentsContainer.stop() chains all four services through
        `finally` blocks, so a service that never started must not raise here —
        it would mask whatever actually went wrong during start.
        """
        service = KeywordService(["Bye"], [], TOPICS, self.event_bus, None)

        service.stop()

    def test_stop_is_idempotent(self):
        self.start(["Bye"])

        self.service.stop()
        self.service.stop()

        self.service = None  # already stopped; keep tearDown off it


class TestKeywordMatching(KeywordServiceTestCase):
    """Matching is on the whole utterance, ungated (intentions=[])."""

    def test_exact_keyword_triggers_quit(self):
        self.start(["Bye"])

        self.say("Bye")

        self.assert_quit()

    def test_keyword_match_is_case_insensitive(self):
        self.start(["Bye"])

        self.say("BYE")

        self.assert_quit()

    def test_surrounding_punctuation_is_ignored(self):
        self.start(["Bye"])

        for text in ("Bye!", "Bye?!", "...bye..."):
            with self.subTest(text=text):
                self.say(text)
                self.assert_quit(f"{text!r} did not match the keyword")

    def test_surrounding_whitespace_is_ignored(self):
        """A trailing space is what ASR transcripts and chat input actually carry."""
        self.start(["Bye"])

        for text in ("Bye ", " bye", "  Bye!  ", "\nbye\n"):
            with self.subTest(text=text):
                self.say(text)
                self.assert_quit(f"{text!r} did not match the keyword")

    def test_multi_word_keyword_matches(self):
        """Stripping is two-sided only; the internal space must survive."""
        self.start(["See you later"])

        self.say("see you later")

        self.assert_quit()

    def test_keyword_must_match_the_whole_utterance(self):
        self.start(["Bye"])

        self.say("bye for now")

        self.assert_no_quit("a substring match ended the conversation")

    def test_non_keyword_utterance_publishes_nothing(self):
        self.start(["Bye"])

        self.say("what is the weather")

        self.assert_no_quit()
        self.assertTrue(self.replies.empty(), "an unexpected reply was published")

    def test_empty_keyword_list_never_matches(self):
        """Constructed directly, since from_config rejects an empty list.

        Pins the behaviour the from_config guard exists to prevent: a service
        that starts, subscribes and silently matches nothing.
        """
        self.start([])

        self.say("Bye")

        self.assert_no_quit("a service with no keywords quit anyway")

    def test_punctuation_only_utterance_does_not_match(self):
        """The hazard a blank keyword would open up, asserted from the outside."""
        self.start(["Bye"])

        self.say("...")

        self.assert_no_quit()


class TestQuitResponse(KeywordServiceTestCase):
    def test_match_publishes_quit_desire_and_a_reply(self):
        """Both, in one test: the pairing is the contract.

        The desire ends the conversation and the reply is what the user hears.
        A change that keeps one and drops the other leaves the agent either
        rudely silent or unable to stop.
        """
        self.start(["Bye"])

        self.say("Bye")

        self.assert_quit()
        reply = self.replies.get(timeout=TIMEOUT)
        self.assertIsInstance(reply.payload, TextSignalEvent)

    def test_reply_is_drawn_from_the_goodbye_pool(self):
        self.start(["Bye"])

        self.say("Bye")

        reply = self.replies.get(timeout=TIMEOUT)
        self.assertIn(reply.payload.signal.text, GOODBYE)

    def test_reply_carries_the_scenario_id(self):
        """cltl-emissor-data files every signal by scenario; losing it loses the turn."""
        self.start(["Bye"])

        self.say("Bye", scenario_id="scenario-42")

        reply = self.replies.get(timeout=TIMEOUT)
        self.assertEqual("scenario-42", reply.payload.signal.time.container_id)

    def test_custom_keyword_replies_from_the_default_pool(self):
        """Keywords and the farewell pool are independent settings.

        Saying "enough" gets a farewell from GOODBYE, not an echo of the
        keyword.
        """
        self.start(["enough"])

        self.say("enough")

        self.assert_quit()
        reply = self.replies.get(timeout=TIMEOUT)
        self.assertIn(reply.payload.signal.text, GOODBYE)

    def test_configured_greetings_replace_the_default_pool(self):
        self.start(["Bye"], greetings=["Farewell"])

        self.say("Bye")

        self.assert_quit()
        reply = self.replies.get(timeout=TIMEOUT)
        self.assertEqual("Farewell", reply.payload.signal.text)

    def test_greeting_is_chosen_from_the_whole_pool(self):
        """random.choice over the configured list, not just its first entry."""
        self.start(["Bye"], greetings=["Farewell", "So long"])

        seen = set()
        for _ in range(40):
            self.say("Bye")
            self.assert_quit()
            seen.add(self.replies.get(timeout=TIMEOUT).payload.signal.text)

        self.assertEqual({"Farewell", "So long"}, seen)

    def test_empty_greetings_quits_without_a_farewell(self):
        """The quit desire is unconditional; only the farewell is optional.

        Asserts on the log as well as on the bus. TopicWorker catches and logs
        everything its processor raises, so "no reply was published" is also
        what a crash inside _process looks like — random.choice([]) raises
        IndexError, and without this the test could not tell the two apart.
        """
        self.start(["Bye"], greetings=[])

        with self.assertLogs("cltl.combot.infra.topic_worker", level="ERROR") as logs:
            self.say("Bye")
            self.assert_quit("an empty farewell pool also suppressed the quit desire")
            self.assertTrue(self.replies.empty(), "a farewell was published anyway")
            # assertLogs fails an empty block, so emit one record of our own.
            logging.getLogger("cltl.combot.infra.topic_worker").error("sentinel")

        self.assertEqual(["sentinel"], [record.getMessage() for record in logs.records],
                         "the topic worker logged an error while handling the keyword")


class TestIntentionGating(KeywordServiceTestCase):
    """When the keyword is live.

    SynchronousEventBus dispatches on the publishing thread, so a worker's
    active flag is already flipped by the time `publish` returns — an intention
    and the utterance that follows it need no synchronisation between them.

    Note that an IntentionEvent flips the flag and is then dropped rather than
    processed, so every assertion here is on a *subsequent* text event.
    """

    def intend(self, *labels: str) -> None:
        self.event_bus.publish(
            INTENTION_TOPIC,
            Event.for_payload(IntentionEvent([Intention(label, None) for label in labels])))

    def test_ungated_service_is_active_immediately(self):
        self.start(["Bye"], intentions=[])

        self.say("Bye")

        self.assert_quit()

    def test_gated_service_ignores_keywords_before_its_intention(self):
        self.start(["Bye"], intentions=["eliza"])

        self.say("Bye")

        self.assert_no_quit("the keyword fired before its intention was active")

    def test_gated_service_activates_on_its_intention(self):
        """The regression test for the configurable intentions.

        With the old hardcoded intentions=["chat"] — a label no BDI model in
        this repo produces — this never fired.
        """
        self.start(["Bye"], intentions=["eliza"])

        self.intend("eliza")
        self.say("Bye")

        self.assert_quit()

    def test_gated_service_ignores_an_unrelated_intention(self):
        self.start(["Bye"], intentions=["eliza"])

        self.intend("init")
        self.say("Bye")

        self.assert_no_quit("an unrelated intention activated the service")

    def test_a_later_unrelated_intention_deactivates_the_service(self):
        self.start(["Bye"], intentions=["eliza"])

        self.intend("eliza")
        self.say("Bye")
        self.assert_quit()

        self.intend("init")
        self.say("Bye")

        self.assert_no_quit("the service stayed active after the conversation ended")

    def test_negated_intention_deactivates(self):
        """A "!label" intention starts active and is switched off by that label.

        Reachable from configuration for the first time now that the list is not
        hardcoded, and covered nowhere else in the monorepo.
        """
        self.start(["Bye"], intentions=["!init"])

        self.say("Bye")
        self.assert_quit("a negated intention did not start active")

        self.intend("init")
        self.say("Bye")

        self.assert_no_quit("the negated intention did not deactivate the service")

    def test_intention_event_is_not_treated_as_a_keyword(self):
        """An ungated worker receives intention events too, and must ignore them.

        Newly reachable: TopicWorker short-circuits its intention check when the
        intentions list is empty, so with the old hardcoded list this path never
        ran. _keyword's topic guard is what keeps it harmless.
        """
        self.start(["Bye"], intentions=[])

        self.intend("eliza")

        self.assert_no_quit("an intention event was matched as a keyword")
        self.assertTrue(self.replies.empty(), "an intention event produced a reply")


if __name__ == "__main__":
    unittest.main()
