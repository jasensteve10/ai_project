# Model training — not applicable to the evaluated study

No model was trained in the verified A/B1/B2/B3 experiment. It uses pretrained
Haiku for generation and the pinned pretrained E5 encoder for retrieval. There
are no project training epochs, optimizer, learning rate or training loss curves.

`python -m src.model_training` writes an explicit no-training setup manifest.
`--prepare-encoder` prepares embeddings locally; `--download-model` explicitly
permits downloading the pinned public encoder if it is not cached.

The untracked `models/e5-small-edan-ft` directory is not selected in the experiment
manifest. Without training code, data splits and logs, its name is not evidence of
fine-tuning and it is excluded from the Docker build.
