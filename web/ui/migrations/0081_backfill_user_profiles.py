from django.db import migrations


def create_missing_profiles(apps, schema_editor):
    """Create a UserProfile for every existing user that lacks one. New users get
    theirs from the post_save signal; this covers users who pre-date the model."""
    User = apps.get_model("auth", "User")
    UserProfile = apps.get_model("ui", "UserProfile")
    existing = set(UserProfile.objects.values_list("user_id", flat=True))
    UserProfile.objects.bulk_create(
        [UserProfile(user_id=uid) for uid in
         User.objects.exclude(id__in=existing).values_list("id", flat=True)]
    )


class Migration(migrations.Migration):

    dependencies = [
        ("ui", "0080_alter_userprofile_options_userprofile_created_at_and_more"),
    ]

    operations = [
        migrations.RunPython(create_missing_profiles, migrations.RunPython.noop),
    ]
