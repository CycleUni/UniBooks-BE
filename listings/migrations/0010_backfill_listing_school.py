from django.db import migrations


def forward_func(apps, schema_editor):
    """Backfill Listing.school for every listing created before multi-region.

    Before multi-region, `Listing.school` was never populated: the create view
    called `serializer.save(seller=request.user)` with no school, and the list
    view filtered by `seller__school__name` — the school hanging off User.

    Multi-region moved school onto RegionVerification, so that join no longer
    exists. The list view now filters by the listing's own `school__name` and
    the create view stamps it, but nothing ever filled it in for the rows that
    already existed. Those listings render as "school not set" and, worse, are
    invisible to every school-filtered query: the listing list, and the
    "recently added at your school" section on the home page.

    Using the seller's *current* verified school is not an approximation of
    the old behaviour — it reproduces it exactly. The pre-multi-region filter
    read `seller.school` live at query time, so a listing was always shown
    under whatever school its seller belonged to right now, not the one they
    belonged to when they posted it.

    Sellers with no active verification in the listing's region keep a null
    school, which is the honest answer: there is no school to attribute.
    """
    Listing = apps.get_model('listings', 'Listing')
    RegionVerification = apps.get_model('accounts', 'RegionVerification')

    # (user_id, region_id) -> school_id, for verifications that actually count.
    school_by_user_region = {
        (user_id, region_id): school_id
        for user_id, region_id, school_id in RegionVerification.objects.filter(
            is_active=True,
            verified_at__isnull=False,
            school__isnull=False,
        ).values_list('user_id', 'region_id', 'school_id')
    }
    if not school_by_user_region:
        return

    to_update = []
    for listing in Listing.objects.filter(school__isnull=True).only(
        'id', 'seller_id', 'region_id', 'school_id'
    ).iterator(chunk_size=2000):
        school_id = school_by_user_region.get((listing.seller_id, listing.region_id))
        if school_id is not None:
            listing.school_id = school_id
            to_update.append(listing)
            if len(to_update) >= 2000:
                Listing.objects.bulk_update(to_update, ['school'])
                to_update = []
    if to_update:
        Listing.objects.bulk_update(to_update, ['school'])


def reverse_func(apps, schema_editor):
    """Deliberately a no-op.

    Reversing would mean nulling `school` on listings, but this migration
    cannot tell the rows it filled from the ones the create view stamped, so
    undoing it would destroy data it never wrote.
    """


class Migration(migrations.Migration):
    dependencies = [
        ('listings', '0009_alter_listing_currency_alter_listing_region'),
        ('accounts', '0011_alter_regionverification_edu_email'),
    ]

    operations = [
        migrations.RunPython(forward_func, reverse_func),
    ]
