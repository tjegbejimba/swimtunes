"""Build-time release trust anchors.

The release workflow rewrites this file before freezing the Windows app. The
committed defaults keep development builds from checking the public update feed
unless explicit environment overrides are provided.
"""

UPDATE_PUBLIC_KEY = ""
UPDATE_PUBLISHER = ""
UPDATE_REPOSITORY = "tjegbejimba/swimtunes-releases"
