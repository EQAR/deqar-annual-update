#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
import sys
import datetime
import psycopg2
import psycopg2.extras
import urllib.parse
import base64
from flask import Flask, request, abort, render_template, g, abort

app = Flask(__name__)

app.config.from_prefixed_env(prefix='DEQAR')

def get_db_cursor():
    if 'db' not in g:
        # connect to PostgreSQL
        dsn = dict( host     =  app.config.get('DB_HOST', 'localhost'),
                    dbname   =  app.config.get('DB_NAME', 'deqar'),
                    user     =  app.config.get('DB_USER', 'deqar'),
                    password =  app.config.get('DB_PASSWORD', None) )
        g.db = psycopg2.connect(**dsn, cursor_factory=psycopg2.extras.DictCursor)
    return g.db.cursor()

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def get_reports_per_year(cur, agency_id, year):
    cur.execute("""
        select
            count(deqar_reports.id) as reports
        from deqar_reports
        left join deqar_reports_contributing_agencies ON deqar_reports_contributing_agencies.report_id = deqar_reports.id
        where (deqar_reports.agency_id = %(agency_id)s or deqar_reports_contributing_agencies.agency_id = %(agency_id)s)
            and ( deqar_reports.valid_from between %(date_from)s and %(date_to)s )
    """, {
        'date_from': datetime.date(year, 1, 1),
        'date_to': datetime.date(year, 12, 31),
        'agency_id': agency_id
    })
    row = cur.fetchone()
    if row is None:
        return 0
    else:
        return row['reports']

@app.route('/form/<int:agency_id>', methods=['GET'])
def make_update_form(agency_id):
    """
    Generates simple HTML forms that redirects to a pre-filled form.

    (uses HTTP POST instead of redirect URLs, as they can grow too long)
    """
    cur = get_db_cursor()
    reference_year = int(app.config.get('REF_YEAR', datetime.date.today().year - 1))

    cur.execute("""
        select
            agency_acronym,
            agency_name,
            agency_id,
            email,
            username,
            case when max(reports_total) > 0 then true else false end as in_deqar,
            max(reports_total) as reports_total,
            sum(reports_year) as reports_year,
            json_agg(json_build_object(
                'iso_3166_alpha3', coalesce(iso_3166_alpha3,'   '),
                'country', coalesce(name_english, ''),
                'type', activity_type,
                'activity', activity,
                'reports', reports_year
            )) as deqar_info
        from (
            select
                deqar_agencies.id as agency_id,
                deqar_agencies.acronym_primary as agency_acronym,
                deqar_agencies.name_primary as agency_name,
                deqar_agencies.reports_total,
                deqar_agencies.email,
                deqar_agencies.username,
                deqar_countries.iso_3166_alpha3,
                deqar_countries.name_english,
                deqar_agency_activity_types.type as activity_type,
                deqar_agency_esg_activities.activity,
                count(distinct deqar_reports.id) as reports_year
            from (
                select
                    deqar_agencies.id,
                    deqar_agencies.acronym_primary,
                    deqar_agencies.name_primary,
                    count(distinct deqar_reports.id) as reports_total,
                    auth_user.email,
                    auth_user.username
                from deqar_agencies
                left join deqar_reports on deqar_reports.agency_id = deqar_agencies.id
                left join deqar_agency_submitting_agencies on deqar_agency_submitting_agencies.agency_id = deqar_agencies.id
                left join accounts_deqarprofile on accounts_deqarprofile.submitting_agency_id = deqar_agency_submitting_agencies.id
                left join auth_user on auth_user.id = accounts_deqarprofile.user_id
                where is_registered and deqar_agencies.id = %(agency_id)s
                group by
                    deqar_agencies.id,
                    name_primary,
                    acronym_primary,
                    email,
                    username
            ) as deqar_agencies
            left join deqar_agency_esg_activities on deqar_agency_esg_activities.agency_id = deqar_agencies.id
            left join deqar_agency_activity_types on deqar_agency_activity_types.id = deqar_agency_esg_activities.activity_type_id
            left join deqar_reports on ( deqar_reports.agency_esg_activity_id = deqar_agency_esg_activities.id
                and ( deqar_reports.valid_from between %(date_from)s and %(date_to)s ) )
            left join deqar_reports_institutions on deqar_reports_institutions.report_id = deqar_reports.id
            left join deqar_institution_countries on deqar_institution_countries.institution_id = deqar_reports_institutions.institution_id
                and deqar_institution_countries.country_verified
            left join deqar_countries on deqar_countries.id = deqar_institution_countries.country_id
            group by
                deqar_agencies.id,
                deqar_agencies.acronym_primary,
                deqar_agencies.name_primary,
                deqar_agencies.reports_total,
                email,
                username,
                iso_3166_alpha3,
                deqar_countries.name_english,
                deqar_agency_activity_types.type,
                deqar_agency_esg_activities.activity
            order by
                acronym_primary,
                deqar_agency_activity_types.type,
                deqar_agency_esg_activities.activity,
                iso_3166_alpha3
        ) as report_stats
        group by
            agency_acronym,
            agency_name,
            agency_id,
            email,
            username
    """, {
        'date_from': datetime.date(reference_year, 1, 1),
        'date_to': datetime.date(reference_year, 12, 31),
        'agency_id': agency_id
    })

    row = cur.fetchone()
    if row is None:
        abort(404)

    # for agencies in DEQAR, simple stats of last year's reports will be shown in a textarea
    if row['in_deqar']:
        deqar_info = "\n".join( "{0[activity]:41.41} ({0[type]:^15.15}) - {0[iso_3166_alpha3]} {0[country]:15.15} : {0[reports]:4}".format(i) for i in row['deqar_info'])
        reports_this = get_reports_per_year(cur, agency_id, reference_year)
        reports_last = get_reports_per_year(cur, agency_id, reference_year-1)
        unusual_decrease = reports_this < (reports_last / 2)
    else:
        deqar_info = ""
        reports_this = 0
        reports_last = 0
        unusual_decrease = False

    # basic dict with agency's info
    parameters = dict(
        id247=row['agency_id'],
        id4=row['agency_name'],
        id174=row['agency_acronym'],
        id179="https://data.deqar.eu/agency/{}".format(row['agency_id']),
        id182=int(row['in_deqar']),
        id165=deqar_info,
        id222=row['username'],
        id223=row['email'],
        id249=int(unusual_decrease),
        id250=reports_this,
        id251=reports_last,
        id252=reference_year,
        id253=reference_year-1
    )
    # for agencies not in DEQAR, pre-fill ESG activity names for matrix input
    if not row['in_deqar']:
        for i in range(16): # currently, hard limit of 16 activities
            if i < len(row['deqar_info']):
                parameters[f"id{230+i}"] = row['deqar_info'][i]['activity']
            else:
                parameters[f"id225-{i+1}-1"] = '-'
    # render form
    return render_template(
        "annual-update.tmpl",
        agency_name=row['agency_name'],
        agency_acronym=row['agency_acronym'],
        agency_url="https://data.deqar.eu/agency/{}".format(row['agency_id']),
        form=parameters
    )

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=8080)

