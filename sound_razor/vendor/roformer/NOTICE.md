Source: https://github.com/ZFTurbo/Music-Source-Separation-Training
Revision: 84b1eac0887756b4f1a9d7a1ff49105939749ed2

Mel-Band RoFormer architecture and attention implementation, vendored under MIT.
Local changes: the attention import is relative, attention uses PyTorch's current
SDPA context manager, and the rotary embedding import narrowly filters the AMP
deprecation warning from the dependency version required by audio-separator.
Model weights are not included.
