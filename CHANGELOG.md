# 0.2.0 (2022-03-17)

## Prelude

Update to accomodate new Python versions and updated dependencies.

## Upgrade Notes

* Python 2.7 support has been dropped. The minimum version of Python now supported by Phabfive is version 3.8.

## Bug Fixes

* [#40](https://github.com/dynamist/phabfive/pull/40) - Update to anyconfig API >= 0.10.0

# 0.1.0 (2019-11-01)

## Prelude

Initial release of Phabfive.

Supported Phabricator app endpoints:

 - passphrase
 - diffusion
 - paste
 - user

## New Features

* [#23](https://github.com/dynamist/phabfive/pull/23) - Function to get clone uri(s) from repo
* [#22](https://github.com/dynamist/phabfive/pull/22) - Functionality to create Paste
* [#21](https://github.com/dynamist/phabfive/pull/21) - Raise exception when Conduit access is not accepted for Passphrase
* [#20](https://github.com/dynamist/phabfive/pull/20) - Add functionality to edit URI
* [#19](https://github.com/dynamist/phabfive/pull/19) - Feature/edit uri
* [#16](https://github.com/dynamist/phabfive/pull/16) - Feature/observe repositories
* [#14](https://github.com/dynamist/phabfive/pull/14) - Print data from user.whoami
* [#12](https://github.com/dynamist/phabfive/pull/12) - Errors now print to stderr
* [#11](https://github.com/dynamist/phabfive/pull/11) - Default to only listing active repositories
* [#10](https://github.com/dynamist/phabfive/pull/10) - Adding shortName
* [#9](https://github.com/dynamist/phabfive/pull/9) - Feature/get specified paste
* [#8](https://github.com/dynamist/phabfive/pull/8) - Repositories can now be created
* [#6](https://github.com/dynamist/phabfive/pull/6) - Avoid string default
* [#5](https://github.com/dynamist/phabfive/pull/5) - Pastes can now be listed, sort based on title
* [#3](https://github.com/dynamist/phabfive/pull/3) - Added Paste app

## Other Notes

* [#24](https://github.com/dynamist/phabfive/pull/24) - Enable RTD build and docs updates
* [#18](https://github.com/dynamist/phabfive/pull/18) - Add code coverage to tox
* [#17](https://github.com/dynamist/phabfive/pull/17) - Proper flake8 with Black
* [#4](https://github.com/dynamist/phabfive/pull/4) - Add encrypted notification config to .travis.yml 
* [#2](https://github.com/dynamist/phabfive/pull/2) - Black-linting
* [#1](https://github.com/dynamist/phabfive/pull/1) - Added travis 
