"""Unit tests for ModelRegistryModel ORM model."""

from datetime import UTC, datetime

from dalston.db.models import ModelRegistryModel


class TestModelRegistryModelAttributes:
    """Tests for ModelRegistryModel attribute definitions."""

    def test_table_name(self):
        """Test the table name is correct."""
        assert ModelRegistryModel.__tablename__ == "models"

    def test_primary_key_is_id(self):
        """Test that id is the primary key."""
        pk_columns = [
            col.name for col in ModelRegistryModel.__table__.primary_key.columns
        ]
        assert pk_columns == ["id"]

    def test_required_columns(self):
        """Test that required columns are marked as non-nullable."""
        table = ModelRegistryModel.__table__

        # Required columns
        assert table.c.id.nullable is False
        assert table.c.engine_id.nullable is False
        assert table.c.loaded_model_id.nullable is False
        assert table.c.stage.nullable is False
        assert table.c.status.nullable is False
        assert table.c.model_metadata.nullable is False
        assert table.c.created_at.nullable is False
        assert table.c.updated_at.nullable is False

    def test_optional_columns(self):
        """Test that optional columns are marked as nullable."""
        table = ModelRegistryModel.__table__

        # Optional columns
        assert table.c.name.nullable is True
        assert table.c.download_path.nullable is True
        assert table.c.size_bytes.nullable is True
        assert table.c.downloaded_at.nullable is True
        assert table.c.source.nullable is True
        assert table.c.library_name.nullable is True
        assert table.c.languages.nullable is True
        assert table.c.min_vram_gb.nullable is True
        assert table.c.min_ram_gb.nullable is True
        assert table.c.last_used_at.nullable is True


class TestModelRegistryModelDefaults:
    """Tests for ModelRegistryModel default values."""

    def test_status_default(self):
        """Test that status defaults to 'not_downloaded'."""
        table = ModelRegistryModel.__table__
        assert table.c.status.server_default.arg == "not_downloaded"

    def test_word_timestamps_default(self):
        """Test that word_timestamps defaults to false."""
        table = ModelRegistryModel.__table__
        assert table.c.word_timestamps.server_default.arg == "false"

    def test_punctuation_default(self):
        """Test that punctuation defaults to false."""
        table = ModelRegistryModel.__table__
        assert table.c.punctuation.server_default.arg == "false"

    def test_native_streaming_default(self):
        """Test that native_streaming defaults to false."""
        table = ModelRegistryModel.__table__
        assert table.c.native_streaming.server_default.arg == "false"

    def test_supports_cpu_default(self):
        """Test that supports_cpu defaults to true."""
        table = ModelRegistryModel.__table__
        assert table.c.supports_cpu.server_default.arg == "true"

    def test_model_metadata_default(self):
        """Test that model_metadata defaults to empty JSON object."""
        table = ModelRegistryModel.__table__
        # model_metadata uses a Python-level callable default (default=dict),
        # not a server_default. Verify the ORM default is a callable that produces {}.
        col_default = table.c.model_metadata.default
        assert col_default is not None
        assert col_default.is_callable is True
        assert table.c.model_metadata.server_default is None


class TestModelRegistryModelIndexes:
    """Tests for ModelRegistryModel indexes."""

    def test_has_engine_id_index(self):
        """Test that engine_id column has an index."""
        table = ModelRegistryModel.__table__
        index_names = [idx.name for idx in table.indexes]
        assert "ix_models_engine_id" in index_names

    def test_has_stage_index(self):
        """Test that stage column has an index."""
        table = ModelRegistryModel.__table__
        index_names = [idx.name for idx in table.indexes]
        assert "ix_models_stage" in index_names

    def test_has_status_index(self):
        """Test that status column has an index."""
        table = ModelRegistryModel.__table__
        index_names = [idx.name for idx in table.indexes]
        assert "ix_models_status" in index_names


class TestModelRegistryModelInstantiation:
    """Tests for instantiating ModelRegistryModel objects."""

    def test_create_minimal_model(self):
        """Test creating a model with only required fields."""
        model = ModelRegistryModel(
            id="faster-whisper-large-v3",
            engine_id="faster-whisper",
            loaded_model_id="Systran/faster-whisper-large-v3",
            stage="transcribe",
        )

        assert model.id == "faster-whisper-large-v3"
        assert model.engine_id == "faster-whisper"
        assert model.loaded_model_id == "Systran/faster-whisper-large-v3"
        assert model.stage == "transcribe"

    def test_create_full_model(self):
        """Test creating a model with all fields."""
        now = datetime.now(UTC)

        model = ModelRegistryModel(
            id="nvidia/parakeet-tdt-1.1b",
            name="Parakeet TDT 1.1B",
            engine_id="nemo",
            loaded_model_id="nvidia/parakeet-tdt-1.1b",
            stage="transcribe",
            status="ready",
            download_path="/models/huggingface/hub/models--nvidia--parakeet-tdt-1.1b",
            size_bytes=4_500_000_000,
            downloaded_at=now,
            source="huggingface",
            library_name="nemo",
            languages=["en"],
            word_timestamps=True,
            punctuation=True,
            native_streaming=False,
            min_vram_gb=4.0,
            min_ram_gb=8.0,
            supports_cpu=False,
            model_metadata={"downloads": 1000, "likes": 50},
            last_used_at=now,
            created_at=now,
            updated_at=now,
        )

        assert model.id == "nvidia/parakeet-tdt-1.1b"
        assert model.name == "Parakeet TDT 1.1B"
        assert model.status == "ready"
        assert model.size_bytes == 4_500_000_000
        assert model.languages == ["en"]
        assert model.word_timestamps is True
        assert model.punctuation is True
        assert model.native_streaming is False
        assert model.min_vram_gb == 4.0
        assert model.supports_cpu is False
        assert model.model_metadata == {"downloads": 1000, "likes": 50}

    def test_model_status_values(self):
        """Test that valid status values can be set."""
        valid_statuses = ["not_downloaded", "downloading", "ready", "failed"]

        for status in valid_statuses:
            model = ModelRegistryModel(
                id=f"test-model-{status}",
                engine_id="test-engine_id",
                loaded_model_id="test/model",
                stage="transcribe",
                status=status,
            )
            assert model.status == status

    def test_model_stage_values(self):
        """Test that valid stage values can be set."""
        valid_stages = ["transcribe", "diarize", "align", "detect", "merge"]

        for stage in valid_stages:
            model = ModelRegistryModel(
                id=f"test-model-{stage}",
                engine_id="test-engine_id",
                loaded_model_id="test/model",
                stage=stage,
            )
            assert model.stage == stage
