"""ContextService carries the tenant of the triggering event into the scenario context."""
import unittest
from unittest.mock import MagicMock

from cltl.combot.event.bdi import Intention, IntentionEvent
from cltl.combot.infra.event import Event, EventMetadata

from cltl_service.context.service import ContextService


class TenantTest(unittest.TestCase):
    def test_tenant_is_set_on_context_and_events(self):
        bus = MagicMock()
        service = ContextService("scenario", "intention", "desire", "speaker", "knowledge", bus, MagicMock())
        service._get_location = lambda: {"country": "", "region": "", "city": ""}

        event = Event.for_payload(IntentionEvent([Intention("init", None)]))
        event = Event.with_tenant(event, "tenant-1")
        service._process(Event.with_topic(event, "intention"))

        published = {call.args[0]: call.args[1] for call in bus.publish.call_args_list}
        self.assertEqual("tenant-1", published["scenario"].payload.scenario.context.tenant)
        self.assertEqual("tenant-1", published["scenario"].metadata.tenant)
        self.assertEqual("tenant-1", published["knowledge"].metadata.tenant)

    def test_tenant_defaults_to_none(self):
        service = ContextService("scenario", "intention", "desire", "speaker", None, MagicMock(), MagicMock())
        service._get_location = lambda: {"country": "", "region": "", "city": ""}
        scenario, _ = service._create_scenario()
        self.assertIsNone(scenario.context.tenant)
