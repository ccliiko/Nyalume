using System;
using System.Collections;
using System.IO;
using System.Runtime.InteropServices;
using UnityEngine;
using UnityEngine.SceneManagement;

// The player contains only this empty startup scene; Eku content stays encrypted on disk.
public class EkuBundledStartup : MonoBehaviour {
    [DllImport("eku_decoder", CallingConvention=CallingConvention.Cdecl)]
    static extern int eku_decode(byte[] encrypted, int length, byte[] output, int capacity);
    AssetBundle bundle;

    byte[] ReadContent() {
        try {
            var path=Path.Combine(Application.streamingAssetsPath,"eku.bin");
            long size=new FileInfo(path).Length;
            if(size<=36 || size>512L*1024*1024) throw new InvalidDataException("Invalid Eku content size");
            var encrypted=File.ReadAllBytes(path);
            var plain=new byte[encrypted.Length-36];
            int count=eku_decode(encrypted,encrypted.Length,plain,plain.Length);
            Array.Clear(encrypted,0,encrypted.Length);
            if(count!=plain.Length) {
                Array.Clear(plain,0,plain.Length);
                throw new InvalidDataException("Eku content authentication failed");
            }
            return plain;
        } catch(Exception error) {
            Debug.LogError("Eku encrypted startup failed: "+error.Message);
            Application.Quit(2);return null;
        }
    }
    IEnumerator Start() {
        Application.runInBackground=true;
        DontDestroyOnLoad(gameObject);
        byte[] plain=ReadContent();
        if(plain==null) yield break;
        var request=AssetBundle.LoadFromMemoryAsync(plain);
        yield return request;
        Array.Clear(plain,0,plain.Length);plain=null;
        bundle=request.assetBundle;
        if(!bundle || bundle.GetAllScenePaths().Length!=1) {
            Debug.LogError("Eku content must contain one desktop scene");
            Application.Quit(2);yield break;
        }
        yield return SceneManager.LoadSceneAsync(bundle.GetAllScenePaths()[0],LoadSceneMode.Single);
        Debug.Log("EKU_ENCRYPTED_CONTENT_READY");
    }
    void OnDestroy() {if(bundle) bundle.Unload(true);}
}
