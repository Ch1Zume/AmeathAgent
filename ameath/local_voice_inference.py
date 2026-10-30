"""Reference-timbre-only GPT-SoVITS v2 inference.

Adapted from AemeathVoice's MIT-licensed `gpt_sovits/inference.py` at
0c0c83f247e79856391fca50a198e67f6d0330e0. The acoustic reference is retained,
but reference audio/text semantic tokens are deliberately not fed to the GPT
decoder: this prevents the reference line from being copied into a reply.
"""
from __future__ import annotations

import numpy as np
import soundfile as sf
import torch
import torchaudio.functional as audio_functional
from module.mel_processing import spectrogram_torch
from text import cleaned_text_to_sequence


class DecodeLimit(Exception):
    pass


class LocalInference:
    def __init__(self, inference, reference):
        self.inference = inference
        self.reference = reference
        self._reference_spec = None

    def reference_spec(self):
        if self._reference_spec is None:
            i = self.inference
            audio, rate = sf.read(str(self.reference), dtype="float32")
            if audio.ndim == 2:
                audio = audio.mean(axis=1)
            audio = torch.from_numpy(np.asarray(audio, dtype=np.float32)).unsqueeze(0).to(i.device)
            sample_rate = int(i.hps.data.sampling_rate)
            if rate != sample_rate:
                audio = audio_functional.resample(audio, rate, sample_rate)
            maximum = audio.abs().max()
            if maximum > 1:
                audio /= min(2, maximum)
            self._reference_spec = spectrogram_torch(audio, i.hps.data.filter_length, sample_rate,
                                                     i.hps.data.hop_length, i.hps.data.win_length,
                                                     center=False).to(i.dtype)
        return self._reference_spec

    @torch.inference_mode()
    def synthesize(self, text, *, top_k, top_p, temperature):
        i = self.inference
        phones, word2ph, normalized = i.clean_text(text, "zh", i.version)
        if len(phones) < 6:
            phones, word2ph, normalized = i.clean_text("。" + text, "zh", i.version)
        phone_ids = cleaned_text_to_sequence(phones, i.version)
        features = i.get_bert_feature(normalized, word2ph).to(i.device).unsqueeze(0)
        ids = torch.tensor(phone_ids, dtype=torch.long, device=i.device).unsqueeze(0)
        length = torch.tensor([ids.shape[-1]], device=i.device)
        limit = min(600, max(150, len(text.strip()) * 20 + 80))
        i.set_seed(-1)
        semantic, _ = i.t2s_model.model.infer_panel(ids, length, None, features, top_k=top_k,
                                                    top_p=top_p, temperature=temperature,
                                                    repetition_penalty=1.35, early_stop_num=limit)
        if semantic.shape[-1] > limit:
            raise DecodeLimit("no_eos")
        audio = i.vq_model.decode(semantic.unsqueeze(0), ids, [self.reference_spec()], speed=1.0)
        return int(i.hps.data.sampling_rate), audio[0][0].detach().cpu().numpy().astype(np.float32)
