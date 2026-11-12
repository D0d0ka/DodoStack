"""WTForms definitions for authentication and admin workflows."""
from __future__ import annotations

from datetime import datetime

from dodo.web import WebForm
from wtforms import FileField
from wtforms import (
    BooleanField,
    IntegerField,
    MultipleFileField,
    RadioField,
    SelectField,
    SelectMultipleField,
    StringField,
    TextAreaField,
)
from wtforms.validators import (
    DataRequired,
    Length,
    NumberRange,
    Optional,
    Regexp,
    ValidationError,
)

AGE_RATINGS = [
    ("G", "G — General audiences"),
    ("PG", "PG — Parental guidance"),
    ("PG-13", "PG-13 — Parents strongly cautioned"),
    ("R", "R — Restricted"),
    ("NC-17", "NC-17 — Adults only"),
    ("TV-Y", "TV-Y — All children"),
    ("TV-G", "TV-G — General audience"),
    ("TV-PG", "TV-PG — Parental guidance"),
    ("TV-14", "TV-14 — Parents strongly cautioned"),
    ("TV-MA", "TV-MA — Mature audience"),
    ("NR", "NR — Not rated"),
]

SUBTITLE_LANGUAGES = [
    ("en", "English"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("pt", "Portuguese"),
    ("it", "Italian"),
    ("nl", "Dutch"),
    ("pl", "Polish"),
    ("sv", "Swedish"),
    ("tr", "Turkish"),
    ("ru", "Russian"),
    ("ar", "Arabic"),
    ("hi", "Hindi"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("zh", "Chinese"),
]

class MovieForm(WebForm):
    title = StringField("Title", validators=[DataRequired(), Length(max=255)])
    creator = StringField("Creator or production company", validators=[Optional(), Length(max=255)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=8000)])
    release_year = IntegerField(
        "Release year",
        validators=[Optional(), NumberRange(min=1888, max=2100)],
    )
    age_rating = SelectField("Age rating", choices=AGE_RATINGS, default="PG-13")
    runtime = IntegerField(
        "Runtime (minutes)", validators=[Optional(), NumberRange(min=0, max=1440)]
    )
    genre_ids = SelectMultipleField("Genres", coerce=int, validators=[Optional()])
    featured = BooleanField("Feature on the homepage")
    published = BooleanField("Publish")
    video = FileField("Movie video")
    trailer = FileField("Trailer video")
    thumbnail = FileField("Poster / thumbnail")


class ShowForm(WebForm):
    title = StringField("Show title", validators=[DataRequired(), Length(max=255)])
    creator = StringField("Creator or production company", validators=[Optional(), Length(max=255)])
    description = TextAreaField("Show description", validators=[Optional(), Length(max=8000)])
    release_year = IntegerField(
        "Release year", validators=[Optional(), NumberRange(min=1888, max=2100)]
    )
    age_rating = SelectField("Age rating", choices=AGE_RATINGS, default="TV-14")
    genre_ids = SelectMultipleField("Genres", coerce=int, validators=[Optional()])
    featured = BooleanField("Feature on the homepage")
    published = BooleanField("Publish")
    trailer = FileField("Trailer video")
    thumbnail = FileField("Poster / thumbnail")


class SeasonForm(WebForm):
    number = IntegerField("Season number", validators=[Optional(), NumberRange(min=0, max=99)])
    title = StringField("Season title", validators=[Optional(), Length(max=255)])
    description = TextAreaField("Season description", validators=[Optional(), Length(max=4000)])

    def validate_number(self, field):
        if field.data is None:
            raise ValidationError("Season number is required.")
        if field.data < 0 or field.data > 99:
            raise ValidationError("Season number must be between 0 and 99.")


class EpisodeForm(WebForm):
    season_id = SelectField("Season", coerce=int, validators=[DataRequired()])
    number = IntegerField("Episode number", validators=[DataRequired(), NumberRange(min=1, max=999)])
    title = StringField("Episode title", validators=[DataRequired(), Length(max=255)])
    description = TextAreaField("Episode description", validators=[Optional(), Length(max=8000)])
    runtime = IntegerField(
        "Runtime (minutes)", validators=[Optional(), NumberRange(min=0, max=1440)]
    )
    published = BooleanField("Publish episode")
    video = FileField("Episode video")
    thumbnail = FileField("Custom thumbnail")


class BulkUploadForm(WebForm):
    season_id = SelectField("Season", coerce=int, validators=[DataRequired()])
    number_start = IntegerField(
        "Start at episode number", validators=[DataRequired(), NumberRange(min=1, max=900)], default=1
    )
    videos = MultipleFileField("Episode video files")
    publish = BooleanField("Publish uploaded episodes")


class BulkApplyForm(WebForm):
    target = RadioField(
        "Apply to",
        choices=[
            ("show", "Only the show"),
            ("all", "All episodes"),
            ("selected", "Selected episodes"),
        ],
        default="all",
        validators=[DataRequired()],
    )
    episode_ids = StringField("Selected episode IDs", validators=[Optional()])
    copy_description = BooleanField("Copy the show description to episodes")
    copy_age_rating = BooleanField("Copy the age rating")
    copy_published = BooleanField("Copy the published status")
    description = TextAreaField("Show description", validators=[Optional(), Length(max=8000)])
    age_rating = SelectField("Age rating", choices=AGE_RATINGS, default="TV-14")
    published = BooleanField("Published")


class SubtitleForm(WebForm):
    language = StringField(
        "Language code",
        validators=[
            DataRequired(),
            Length(min=2, max=16),
            Regexp(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$", message="Use a language code such as en or pt-BR."),
        ],
    )
    label = StringField("Display name", validators=[DataRequired(), Length(max=96)])
    file = FileField("WebVTT file")
    is_default = BooleanField("Default subtitle track")


class SettingsForm(WebForm):
    site_name = StringField("Site name", validators=[DataRequired(), Length(max=80)])
    tagline = StringField("Tagline", validators=[Optional(), Length(max=160)])
    footer_note = StringField("Footer note", validators=[Optional(), Length(max=255)])
    allow_registration = BooleanField("Allow new Dodo TV profiles")


def default_year() -> int:
    return datetime.now().year
