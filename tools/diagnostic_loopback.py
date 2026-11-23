"""Send resumable diagnostics through the installation's pinned HTTP broker."""
import asyncio
import http.client
import json
import re


def request(options,method,path,token,body):
    from roi_account_client import ISSUER,RegistrationError
    if (options[0]!=ISSUER or method!='POST'
            or re.fullmatch(r'/diagnostics/[1-9][0-9]{0,19}',path) is None
            or not isinstance(token,str) or re.fullmatch('[0-9a-f]{64}',token) is None):
        raise RegistrationError('Invalid diagnostic transport',stage='server-config')
    raw=json.dumps(body,separators=(',',':')).encode()
    if len(raw)>16384:raise RegistrationError('Diagnostic request too large',stage='request-size')
    conn=http.client.HTTPConnection('127.0.0.1',52713,timeout=1.5)
    try:
        conn.request('POST',path,raw,{'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        response=conn.getresponse()
        lengths=response.headers.get_all('Content-Length',[])
        if (len(lengths)!=1 or not lengths[0].isascii() or not lengths[0].isdecimal()
                or int(lengths[0])>16384 or response.headers.get('Transfer-Encoding')):
            raise RegistrationError('Invalid diagnostic framing',stage='response-framing')
        raw=response.read(16385)
        if len(raw)!=int(lengths[0]):
            raise RegistrationError('Incomplete diagnostic response',stage='response-framing')
        if response.status==401:return None
        if response.status!=200:
            raise RegistrationError('Diagnostic service unavailable',stage='http-response',http_status=response.status)
        return json.loads(raw)
    finally:conn.close()


async def exchange(options,method,path,token=None,body=None):
    # No direct-TLS fallback or replay: the existing durable outbox retries later.
    return await asyncio.to_thread(request,options,method,path,token,body)
