import dataclasses
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from runtime_contracts import (Capability, CompatibilityError, CompatibilityRegistry,
    EvidenceScope, ExistingTextAdapter, GIB, GPT_NATIVE_PROBE_MIN_RAM,
    HardwareSnapshot, ModelContract, ResourcePolicy, ValidationEvidence)


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.contract = ModelContract("fixture-model", "fixture-architecture", "fixture-quantization",
            "a"*64, "fixture-runtime", "b"*64, "c"*64, 4096, ("a"*64,), "test-only")
        self.hardware = HardwareSnapshot("d"*64, 8*GIB, 2*GIB, 9*GIB, 55)
        self.evidence = ValidationEvidence(self.contract, "d"*64, EvidenceScope.NATIVE,
            frozenset({Capability.TEXT, Capability.STREAMING}), 2048, "e"*64, ResourcePolicy(GIB, GIB, GIB))
        # This synthetic acceptance service establishes contract behavior only.
        # It does not confer genuine model acceptance on the application.
        self.registry = CompatibilityRegistry(lambda record: record is self.evidence)

    def assess(self, **changes):
        args = dict(contract=self.contract, hardware=self.hardware,
                    capabilities=frozenset({Capability.TEXT}), context=2048)
        args.update(changes)
        return self.registry.assess(**args)

    def test_no_catalog_is_implicitly_validated(self):
        self.assertEqual(self.assess()['status'], 'NOT_VERIFIED')
        with self.assertRaises(CompatibilityError):
            self.registry.register({'architecture':'gpt-oss', 'native':True})

    def test_independent_acceptance_is_required(self):
        with self.assertRaises(CompatibilityError):
            self.registry.register(dataclasses.replace(self.evidence, receipt_sha256='f'*64))
        with self.assertRaises(CompatibilityError):
            CompatibilityRegistry(None)

    def test_truthy_verifier_does_not_pass(self):
        with self.assertRaises(CompatibilityError):
            CompatibilityRegistry(lambda record: 'yes').register(self.evidence)

    def test_exact_scope_is_accepted(self):
        self.registry.register(self.evidence)
        self.assertTrue(self.assess()['allowed'])
        self.assertEqual(self.assess()['receiptSha256'], 'e'*64)

    def test_fixture_and_shell_never_establish_native_support(self):
        for scope in (EvidenceScope.DETERMINISTIC, EvidenceScope.PACKAGED_SHELL):
            registry=CompatibilityRegistry(lambda record: True)
            registry.register(dataclasses.replace(self.evidence, scope=scope))
            self.assertFalse(registry.assess(self.contract,self.hardware,frozenset({Capability.TEXT}),2048)['allowed'])

    def test_changed_model_runtime_template_quantization_assets_are_unverified(self):
        self.registry.register(self.evidence)
        changes = ({'model_sha256':'f'*64,'required_assets':('f'*64,)},
            {'runtime_sha256':'f'*64}, {'template_sha256':'f'*64},
            {'quantization':'different'}, {'architecture':'another'}, {'required_assets':('a'*64,'f'*64)})
        for fields in changes:
            with self.subTest(fields=fields):
                self.assertEqual(self.assess(contract=dataclasses.replace(self.contract,**fields))['status'],'NOT_VERIFIED')

    def test_different_driver_or_hardware_is_unverified(self):
        self.registry.register(self.evidence)
        self.assertFalse(self.assess(hardware=dataclasses.replace(self.hardware,identity_sha256='f'*64))['allowed'])

    def test_unverified_tools_json_and_vision_are_disabled(self):
        self.registry.register(self.evidence)
        for capability in (Capability.TOOLS,Capability.JSON,Capability.VISION):
            self.assertFalse(self.assess(capabilities=frozenset({capability}))['allowed'])

    def test_context_limit_is_actual_tested_limit(self):
        self.registry.register(self.evidence)
        self.assertFalse(self.assess(context=2049)['allowed'])

    def test_low_ram_vram_disk_and_unknown_resources_fail_closed(self):
        self.registry.register(self.evidence)
        for key in ('available_ram','available_vram','free_disk'):
            for value in (GIB-1,None):
                self.assertFalse(self.assess(hardware=dataclasses.replace(self.hardware,**{key:value}))['allowed'])

    def test_cpu_policy_does_not_require_unknown_vram(self):
        registry=CompatibilityRegistry(lambda record:True)
        registry.register(dataclasses.replace(self.evidence,policy=ResourcePolicy(GIB,0,0)))
        hardware=dataclasses.replace(self.hardware,available_vram=None,free_disk=None)
        self.assertTrue(registry.assess(self.contract,hardware,frozenset({Capability.TEXT}),2048)['allowed'])

    def test_gpt_probe_keeps_seven_point_five_gib_gate(self):
        self.assertEqual(GPT_NATIVE_PROBE_MIN_RAM,8053063680)
        self.registry.register(self.evidence)
        hardware=dataclasses.replace(self.hardware,available_ram=GPT_NATIVE_PROBE_MIN_RAM-1)
        self.assertTrue(self.assess(hardware=hardware)['allowed']) # Smaller-model policy is independent.
        self.assertFalse(self.assess(hardware=hardware,gpt_native_probe=True)['allowed'])
        hardware=dataclasses.replace(hardware,available_ram=GPT_NATIVE_PROBE_MIN_RAM)
        self.assertTrue(self.assess(hardware=hardware,gpt_native_probe=True)['allowed'])

    def test_gpt_probe_requires_cool_measured_start(self):
        self.registry.register(self.evidence)
        for temperature in (70,82,None):
            self.assertFalse(self.assess(hardware=dataclasses.replace(self.hardware,gpu_temperature_c=temperature),gpt_native_probe=True)['allowed'])

    def test_no_implicit_evidence_replacement(self):
        self.registry.register(self.evidence)
        with self.assertRaises(CompatibilityError):self.registry.register(self.evidence)

    def test_contracts_are_immutable(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):self.contract.model_id='other'
        with self.assertRaises(CompatibilityError):dataclasses.replace(self.contract,required_assets=['a'*64])

    def test_malformed_digest_and_assets_rejected(self):
        for fields in ({'model_sha256':'../../x'}, {'runtime_sha256':'A'*64},
                       {'required_assets':('f'*64,)}, {'required_assets':('a'*64,'a'*64)}):
            with self.assertRaises(CompatibilityError):dataclasses.replace(self.contract,**fields)

    def test_invalid_context_and_resource_values_rejected(self):
        for value in (True,0,-1,1.5,'4096'):
            with self.assertRaises(CompatibilityError):self.assess(context=value)
        for value in (True,-1,float('nan'),'1'):
            with self.assertRaises(CompatibilityError):ResourcePolicy(value,0,0)
        for value in (float('nan'),float('inf'),True,'60'):
            with self.assertRaises(CompatibilityError):dataclasses.replace(self.hardware,gpu_temperature_c=value)

    def test_scope_and_capability_strings_are_not_evidence(self):
        for fields in ({'scope':'native'}, {'capabilities':frozenset({'text'})}, {'capabilities':set()}, {'tested_context':8192}):
            with self.assertRaises(CompatibilityError):dataclasses.replace(self.evidence,**fields)
        with self.assertRaises(CompatibilityError):self.assess(capabilities=frozenset({'text'}))


class ExistingAdapterTests(unittest.TestCase):
    def setUp(self):
        self.manager=SimpleNamespace(runtime=SimpleNamespace(ready=True,context=3072,complete=Mock(return_value='result')),
                                     lm_runtime=SimpleNamespace(complete=Mock(return_value='external')),chat_memory_profile={'batch':32})
        self.messages=[{'role':'user','content':'Explain TCP.'}]
        self.kwargs=dict(max_tokens=256,temperature=.2,cancel_event=object(),on_token=Mock(),progress=Mock(),
                         connection_callback=Mock(),top_p=.8,allowed_tools=['calculator'],reasoning_effort=None)

    def test_bundled_argument_stream_and_cancellation_identity_preserved(self):
        result=ExistingTextAdapter(self.manager,'bundled').complete(self.messages,**self.kwargs)
        self.assertEqual(result,'result')
        args,kwargs=self.manager.runtime.complete.call_args
        self.assertEqual(args,(self.messages,3072,256,.2,self.kwargs['cancel_event'],self.kwargs['on_token'],self.kwargs['progress'],self.manager.chat_memory_profile))
        self.assertEqual(kwargs,{k:self.kwargs[k] for k in ('connection_callback','top_p','allowed_tools','reasoning_effort')})
        self.manager.lm_runtime.complete.assert_not_called()

    def test_lmstudio_defaults_and_explicit_reasoning_preserved(self):
        adapter=ExistingTextAdapter(self.manager,'lmstudio')
        self.assertEqual(adapter.complete(self.messages,**self.kwargs),'external')
        args,kwargs=self.manager.lm_runtime.complete.call_args
        self.assertEqual(args,(self.messages,256,.2,self.kwargs['cancel_event'],self.kwargs['on_token'],self.kwargs['progress']))
        self.assertEqual(kwargs['reasoning_effort'],'low')
        adapter.complete(self.messages,**{**self.kwargs,'reasoning_effort':'high'})
        self.assertEqual(self.manager.lm_runtime.complete.call_args.kwargs['reasoning_effort'],'high')
        self.manager.runtime.complete.assert_not_called()

    def test_tool_result_object_is_returned_without_reinterpretation(self):
        sentinel=object();self.manager.runtime.complete.return_value=sentinel
        self.assertIs(ExistingTextAdapter(self.manager,'bundled').complete(self.messages,**self.kwargs),sentinel)

    def test_runtime_errors_are_not_retried_or_hidden(self):
        self.manager.runtime.complete.side_effect=RuntimeError('Cancelled')
        with self.assertRaisesRegex(RuntimeError,'Cancelled'):
            ExistingTextAdapter(self.manager,'bundled').complete(self.messages,**self.kwargs)
        self.assertEqual(self.manager.runtime.complete.call_count,1)

    def test_stopped_bundled_runtime_does_not_call_any_backend(self):
        self.manager.runtime.ready=False
        with self.assertRaisesRegex(RuntimeError,'stopped'):
            ExistingTextAdapter(self.manager,'bundled').complete(self.messages,**self.kwargs)
        self.manager.runtime.complete.assert_not_called();self.manager.lm_runtime.complete.assert_not_called()

    def test_unknown_runtime_never_falls_back_to_cloud_or_other_engine(self):
        with self.assertRaises(CompatibilityError):ExistingTextAdapter(self.manager,'unverified-runtime')
        self.manager.runtime.complete.assert_not_called();self.manager.lm_runtime.complete.assert_not_called()


if __name__=='__main__':unittest.main()
