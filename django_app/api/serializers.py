from rest_framework import serializers
from .models import MainChannel, MainGroup, DraftChannel, TaskTime, ChannelPostTask
from telegram.models import BotSession


# ==================== Вспомогательные сериализаторы ====================

class TaskTimeSerializer(serializers.ModelSerializer):
    """
    Сериализатор для таймингов задачи публикации.
    Используется только для чтения (times) в ChannelPostTaskSerializer.
    """
    class Meta:
        model = TaskTime
        fields = ["id", "weekday", "seconds_from_day_start"]


class BotTokenSerializer(serializers.ModelSerializer):
    """
    Сериализатор для модели BotSession.
    Используется как вложенный объект bot_info.
    """
    class Meta:
        model = BotSession
        fields = "__all__"


# ==================== ChannelPostTask ====================

class ChannelPostTaskSerializer(serializers.ModelSerializer):
    """
    Сериализатор для задач публикации (ChannelPostTask).
    Поддерживает создание/обновление с указанием:
    - source_model / source_id (источник: канал или группа)
    - target_model / target_id (цель: канал или группа)
    - times_input (список словарей {weekday, seconds_from_day_start})
    """

    # Входные данные
    bot = serializers.PrimaryKeyRelatedField(
        queryset=BotSession.objects.all(), write_only=True)
    source_model = serializers.CharField(
        write_only=True, required=True)  # "channel" | "group"
    source_id = serializers.IntegerField(write_only=True, required=True)
    target_model = serializers.CharField(
        write_only=True, required=True)  # "channel" | "group"
    target_id = serializers.IntegerField(write_only=True, required=True)

    # На чтение
    bot_info = BotTokenSerializer(source="bot", read_only=True)
    times = TaskTimeSerializer(many=True, read_only=True)  # связанные TaskTime
    source = serializers.SerializerMethodField(
        read_only=True)  # {"id":..., "name":..., "model":...}
    target = serializers.SerializerMethodField(read_only=True)

    # Для записи — список словарей {weekday, seconds_from_day_start}
    times_input = serializers.ListField(
        child=serializers.DictField(),
        write_only=True,
        required=False,
    )

    class Meta:
        model = ChannelPostTask
        fields = [
            "id", "bot", "bot_info", "task_type", "choice_mode", "after_publish",
            "source_model", "source_id", "target_model", "target_id",
            "times", "times_input", "source", "target",
        ]

    # ---------- Создание ----------
    def create(self, validated_data):
        times_data = validated_data.pop("times_input", [])
        source_model = validated_data.pop("source_model")
        source_id = validated_data.pop("source_id")
        target_model = validated_data.pop("target_model")
        target_id = validated_data.pop("target_id")

        task_kwargs = {**validated_data}

        # Источник
        if source_model == "channel":
            task_kwargs["source_channel_id"] = source_id
        elif source_model == "group":
            task_kwargs["source_group_id"] = source_id

        # Цель
        if target_model == "channel":
            task_kwargs["target_channel_id"] = target_id
        elif target_model == "group":
            task_kwargs["target_group_id"] = target_id

        task = ChannelPostTask.objects.create(**task_kwargs)

        # создаём тайминги
        for t in times_data:
            TaskTime.objects.create(
                task=task,
                weekday=t["weekday"],
                seconds_from_day_start=t["seconds_from_day_start"],
            )
        return task

    # ---------- Обновление ----------
    def update(self, instance, validated_data):
        times_data = validated_data.pop("times_input", None)
        source_model = validated_data.pop("source_model", None)
        source_id = validated_data.pop("source_id", None)
        target_model = validated_data.pop("target_model", None)
        target_id = validated_data.pop("target_id", None)

        # Обновляем источник
        if source_model and source_id:
            if source_model == "channel":
                instance.source_channel_id = source_id
                instance.source_group = None
            elif source_model == "group":
                instance.source_group_id = source_id
                instance.source_channel = None

        # Обновляем цель
        if target_model and target_id:
            if target_model == "channel":
                instance.target_channel_id = target_id
                instance.target_group = None
            elif target_model == "group":
                instance.target_group_id = target_id
                instance.target_channel = None

        # Сохраняем остальные поля (task_type, choice_mode, after_publish и т.д.)
        instance = super().update(instance, validated_data)

        # Перезаписываем тайминги
        if times_data is not None:
            instance.times.all().delete()
            for t in times_data:
                TaskTime.objects.create(
                    task=instance,
                    weekday=t["weekday"],
                    seconds_from_day_start=t["seconds_from_day_start"],
                )

        return instance

    # ---------- Поля на чтение ----------
    def get_source(self, obj):
        """
        Возвращает словарь с данными источника:
        {"id":..., "name":..., "model": "channel|group"}
        """
        if obj.source_channel:
            return {"id": obj.source_channel.id, "name": obj.source_channel.name, "model": "channel"}
        if obj.source_group:
            return {"id": obj.source_group.id, "name": obj.source_group.name, "model": "group"}
        return None

    def get_target(self, obj):
        """
        Возвращает словарь с данными цели:
        {"id":..., "name":..., "model": "channel|group"}
        """
        if obj.target_channel:
            return {"id": obj.target_channel.id, "name": obj.target_channel.name, "model": "channel"}
        if obj.target_group:
            return {"id": obj.target_group.id, "name": obj.target_group.name, "model": "group"}
        return None


# ==================== MainChannel / MainGroup ====================

class MainChannelSerializer(serializers.ModelSerializer):
    """
    Сериализатор для MainChannel.
    Добавляет поле type="channel".
    """
    type = serializers.SerializerMethodField()

    class Meta:
        model = MainChannel
        fields = "__all__"

    def get_type(self, obj):
        return "channel"


class MainGroupSerializer(serializers.ModelSerializer):
    """
    Сериализатор для MainGroup.
    Добавляет поле type="group".
    """
    type = serializers.SerializerMethodField()

    class Meta:
        model = MainGroup
        fields = "__all__"

    def get_type(self, obj):
        return "group"


# ==================== DraftChannel ====================

class DraftChannelSerializer(serializers.ModelSerializer):
    """
    Сериализатор для DraftChannel.
    Поле main_group_ids разворачивается в список ID (ManyToMany).
    """

    main_group_ids = serializers.PrimaryKeyRelatedField(
        source="main_groups",
        many=True,
        queryset=MainGroup.objects.all(),
        required=False,
    )

    class Meta:
        model = DraftChannel
        fields = [
            "id", "name", "telegram_id", "description", "photo",
            "last_published_at", "link", "error_count", "category", "country", "tags",
            "choice", "after_publish",
            "main_channel", "main_group_ids",
        ]
