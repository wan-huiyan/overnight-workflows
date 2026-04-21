-- Country-bucket decomposition CTE (canonical, from v4 C9 / F09c).
-- Added v1.6.0 (S98, 2026-04-21).
--
-- Reusable pattern for any v5+ candidate that needs to decompose an
-- International cohort by region. Normalises country_of_citizenship variants
-- (e.g. "United States of America" vs "USA" vs "US") before bucketing, then
-- buckets by region.
--
-- Usage: substitute `base_panel` with the stitched view / candidate base,
-- join to the appropriate SF account table, then cross-tab the bucket against
-- realized enrollment + compute bootstrap CI for lift vs US_Domestic.

WITH country_normalised AS (
  SELECT
    p.visitor_id,
    CASE
      WHEN TRIM(sa.country_of_citizenship) IN (
        'United States', 'United States of America', 'USA', 'US'
      )
        THEN 'US_Domestic'
      WHEN sa.country_of_citizenship IN (
        'Nigeria','Ghana','Kenya','Ethiopia','Rwanda',
        'Cameroon','Uganda','Tanzania','Zimbabwe','Zambia'
      )
        THEN 'Africa'
      WHEN sa.country_of_citizenship IN (
        'Bahamas','Jamaica','Haiti','Trinidad and Tobago',
        'Barbados','Dominican Republic'
      )
        THEN 'Caribbean'
      WHEN sa.country_of_citizenship IN (
        'Brazil','Venezuela','Colombia','Argentina',
        'Peru','Mexico','Ecuador','Chile'
      )
        THEN 'LatinAmerica'
      WHEN sa.country_of_citizenship IN (
        'Uzbekistan','Pakistan','India','Bangladesh',
        'Sri Lanka','Nepal','Kazakhstan'
      )
        THEN 'AsiaSC'
      WHEN sa.country_of_citizenship IS NULL
        THEN 'Unknown'
      ELSE 'Other_International'
    END AS country_bucket
  FROM base_panel p
  LEFT JOIN `barry-cdp.bloomreach_imports.salesforce_adm_account` sa
    ON LOWER(sa.id) = LOWER(p.salesforce_account_id)
)
-- downstream: cross-tab country_bucket vs realized enrollment,
-- compute bootstrap CI for lift vs US_Domestic (n_boot >= 1000).
SELECT country_bucket, COUNT(*) AS n
FROM country_normalised
GROUP BY country_bucket
ORDER BY n DESC;
