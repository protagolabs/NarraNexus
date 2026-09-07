from narranexus.sdk import Contribution, SettingField, SettingsSchema

# Values resolve NXP___PLUGIN_PKG____<KEY> env > stored row > default; secrets are encrypted at rest.
SETTINGS = (
    Contribution(
        "schema",
        lambda: SettingsSchema(
            {
                "api_key": SettingField("string", secret=True, description="Service API key"),
                "interval_minutes": SettingField("integer", default=15, description="Sync interval"),
                "region": SettingField("enum", default="eu", choices=("eu", "us")),
            }
        ),
    ),
)


def activate(ctx):
    ctx.log.info(f"interval={ctx.settings.get('interval_minutes')} region={ctx.settings.get('region')}")
