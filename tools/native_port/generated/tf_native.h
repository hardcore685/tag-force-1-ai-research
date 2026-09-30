#ifndef TF_NATIVE_H
#define TF_NATIVE_H
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <math.h>
#ifdef _WIN32
#define TF_EXPORT __declspec(dllexport)
#else
#define TF_EXPORT __attribute__((visibility("default")))
#endif
typedef struct TFContext {
    uint32_t r[32], f[32], hi, lo, pc, fcr31;
    uint32_t error, fault_pc, fault_word, fault_address, resume_pc;
    uint64_t steps, max_steps;
    uint8_t *mem;
    uint32_t mem_start, mem_size;
    uint32_t *coverage;
    uint32_t coverage_size;
    uint8_t *hooks;
    uint32_t hook_size;
} TFContext;
enum { TF_OK=0, TF_MEMORY=1, TF_UNSUPPORTED=2, TF_SYSCALL=3,
       TF_BUDGET=4, TF_BRANCH_SLOT=5, TF_CODE_ADDRESS=6, TF_HOOK=7 };
static inline void tf_fail(TFContext*c,uint32_t error,uint32_t word,uint32_t address){
    if(!c->error){c->error=error;c->fault_pc=c->pc;c->fault_word=word;c->fault_address=address;}
}
static inline uint32_t tf_offset(TFContext*c,uint32_t a,uint32_t n){
    a &= 0x3fffffffu;
    if(a<c->mem_start || n>c->mem_size || a-c->mem_start>c->mem_size-n){
        tf_fail(c,TF_MEMORY,0,a); return UINT32_MAX;
    }
    return a-c->mem_start;
}
static inline uint32_t tf_load(TFContext*c,uint32_t a,unsigned n){
    uint32_t i=tf_offset(c,a,n),v=0;
    if(i==UINT32_MAX)return 0;
    for(unsigned j=0;j<n;j++)v|=(uint32_t)c->mem[i+j]<<(j*8);
    return v;
}
static inline void tf_store(TFContext*c,uint32_t a,uint32_t v,unsigned n){
    uint32_t i=tf_offset(c,a,n);if(i==UINT32_MAX)return;
    for(unsigned j=0;j<n;j++)c->mem[i+j]=(uint8_t)(v>>(j*8));
}
static inline int tf_tick(TFContext*c,uint32_t pc){
    c->pc=pc;
    if(c->error)return 1;
    if(c->hooks){uint32_t i=(pc-c->mem_start)/4;if(i<c->hook_size&&c->hooks[i]){tf_fail(c,TF_HOOK,0,pc);return 1;}}
    if(c->steps>=c->max_steps){tf_fail(c,TF_BUDGET,0,pc);return 1;}
    c->steps++;
    if(c->coverage){uint32_t i=(pc-c->mem_start)/4;if(i<c->coverage_size)c->coverage[i]++;}
    return 0;
}
static inline uint32_t tf_rot(uint32_t v,unsigned s){return s?(v>>s)|(v<<(32-s)):v;}
static inline uint32_t tf_clz(uint32_t v){uint32_t n=0;if(!v)return 32;while(!(v&0x80000000u)){n++;v<<=1;}return n;}
static inline uint32_t tf_swap16(uint32_t v){return ((v&0x00ff00ffu)<<8)|((v&0xff00ff00u)>>8);}
static inline uint32_t tf_swap32(uint32_t v){return tf_rot(tf_swap16(v),16);}
static inline uint32_t tf_bitrev(uint32_t v){uint32_t out=0;for(unsigned i=0;i<32;i++){out=(out<<1)|(v&1u);v>>=1;}return out;}
static inline void tf_div(TFContext*c,uint32_t ua,uint32_t ub,int sign){
    if(sign){int64_t a=(int32_t)ua,b=(int32_t)ub;
        if(a==INT32_MIN&&b== -1){c->lo=0x80000000u;c->hi=UINT32_MAX;}
        else if(b){int64_t q=a/b;c->lo=(uint32_t)q;c->hi=(uint32_t)(a-q*b);}
        else{c->lo=a<0?1u:UINT32_MAX;c->hi=ua;}
    }else if(ub){c->lo=ua/ub;c->hi=ua%ub;}else{c->lo=ua<=65535u?65535u:UINT32_MAX;c->hi=ua;}
}
static inline float tf_float(uint32_t v){float f;memcpy(&f,&v,4);return f;}
static inline uint32_t tf_bits(float f){uint32_t v;memcpy(&v,&f,4);return v;}
static inline uint32_t tf_cvt(float x,unsigned mode){
    double y=mode==1?trunc((double)x):mode==2?ceil((double)x):mode==3?floor((double)x):nearbyint((double)x);
    if(!isfinite(y)||y>2147483647.0||y< -2147483648.0)return 0x80000000u;
    return (uint32_t)(int32_t)y;
}
TF_EXPORT uint32_t tf_run(TFContext *ctx);
TF_EXPORT uint32_t tf_abi_version(void);
#endif
