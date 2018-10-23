# -*- coding: utf-8 -*-


class PhabfiveException(Exception):
    pass


class PhabfiveDataException(PhabfiveException):
    pass


class PhabfiveConfigException(PhabfiveException):
    pass


class PhabfiveRemoteException(PhabfiveException):
    pass
